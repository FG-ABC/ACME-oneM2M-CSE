#
#	Dispatcher.py
#
#	(c) 2020 by Andreas Kraft
#	License: BSD 3-Clause License. See the LICENSE file for further details.
#
#	Most internal requests are routed through here.
#

from __future__ import annotations
import sys
from copy import deepcopy
from typing import Any, List, Tuple, Dict, cast

from ..helpers import TextTools as TextTools
from ..etc.Constants import Constants as C
from ..etc.Types import ResourceTypes as T
from ..etc.Types import FilterOperation
from ..etc.Types import Permission
from ..etc.Types import DesiredIdentifierResultType as DRT
from ..etc.Types import ResultContentType as RCN
from ..etc.Types import ResponseStatusCode as RC
from ..etc.Types import Result
from ..etc.Types import CSERequest
from ..etc.Types import JSON, Parameters, Conditions
from ..etc import Utils
from ..etc import DateUtils
from ..services import CSE
from ..services.Configuration import Configuration
from ..services.Logging import Logging as L
from ..resources import Factory as Factory
from ..resources.Resource import Resource


class Dispatcher(object):

	def __init__(self) -> None:
		self.csiSlashLen 				= len(CSE.cseCsiSlash)
		self.sortDiscoveryResources 	= Configuration.get('cse.sortDiscoveredResources')
		L.isInfo and L.log('Dispatcher initialized')


	def shutdown(self) -> bool:
		L.isInfo and L.log('Dispatcher shut down')
		return True



	# The "xxxRequest" methods handle http requests while the "xxxResource"
	# methods handle actions on the resources. Security/permission checking
	# is done for requests, not on resource actions.


	#########################################################################

	#
	#	Retrieve resources
	#

	def processRetrieveRequest(self, request:CSERequest, originator:str, id:str=None) -> Result:
		srn, id = self._checkHybridID(request, id) # overwrite id if another is given

		# Handle operation execution time and check request expiration
		self._handleOperationExecutionTime(request)
		if not (res := self._checkRequestExpiration(request)).status:
			return res

		# handle fanout point requests
		if (fanoutPointResource := Utils.fanoutPointResource(srn)) and fanoutPointResource.ty == T.GRP_FOPT:
			L.isDebug and L.logDebug(f'Redirecting request to fanout point: {fanoutPointResource.__srn__}')
			return fanoutPointResource.handleRetrieveRequest(request, srn, request.headers.originator)

		# Handle PollingChannelURI RETRIEVE
		if (pollingChannelURIResource := Utils.pollingChannelURIResource(srn)):		# We need to check the srn here
			if not CSE.security.hasAccessToPollingChannel(originator, pollingChannelURIResource):
				L.logDebug(dbg:=f'Originator: {originator} has not access to <pollingChannelURI>: {id}')
				return Result(status=False, rsc=RC.originatorHasNoPrivilege, dbg=dbg)
			L.isDebug and L.logDebug(f'Redirecting request <PCU>: {pollingChannelURIResource.__srn__}')
			return pollingChannelURIResource.handleRetrieveRequest(request, id, originator)

		permission = Permission.DISCOVERY if request.args.fu == 1 else Permission.RETRIEVE

		# check rcn & operation
		if permission == Permission.DISCOVERY and request.args.rcn not in [ RCN.discoveryResultReferences, RCN.childResourceReferences ]:	# Only allow those two
			return Result(status=False, rsc=RC.badRequest, dbg=f'invalid rcn: {int(request.args.rcn)} for fu: {int(request.args.fu)}')
		if permission == Permission.RETRIEVE and request.args.rcn not in [ RCN.attributes, RCN.attributesAndChildResources, RCN.childResources, RCN.attributesAndChildResourceReferences, RCN.originalResource, RCN.childResourceReferences]: # TODO
			return Result(status=False, rsc=RC.badRequest, dbg=f'invalid rcn: {int(request.args.rcn)} for fu: {int(request.args.fu)}')

		L.isDebug and L.logDebug(f'Discover/Retrieve resources (rcn: {request.args.rcn}, fu: {request.args.fu.name}, drt: {request.args.drt.name}, handling: {request.args.handling}, conditions: {request.args.conditions}, resultContent: {request.args.rcn.name}, attributes: {str(request.args.attributes)})')

		# Retrieve the target resource, because it is needed for some rcn (and the default)
		if request.args.rcn in [RCN.attributes, RCN.attributesAndChildResources, RCN.childResources, RCN.attributesAndChildResourceReferences, RCN.originalResource]:
			if not (res := self.retrieveResource(id, originator, request)).status:
			 	return res # error
			if not CSE.security.hasAccess(originator, res.resource, permission):
				return Result(status=False, rsc=RC.originatorHasNoPrivilege, dbg=f'originator has no permission ({permission})')

			# if rcn == attributes then we can return here, whatever the result is
			if request.args.rcn == RCN.attributes:
				if not (resCheck := res.resource.willBeRetrieved(originator)).status:	# resource instance may be changed in this call
					return resCheck
				return res

			resource = res.resource	# root resource for the retrieval/discovery

			# if rcn == original-resource we retrieve the linked resource
			if request.args.rcn == RCN.originalResource:
				if not resource:	# continue only when there actually is a resource
					return res
				if not (lnk := resource.lnk):	# no link attribute?
					return Result(status=False, rsc=RC.badRequest, dbg='missing lnk attribute in target resource')

				# Retrieve and check the linked-to request
				if (res := self.retrieveResource(lnk, originator, request)).resource:
					if not (resCheck := res.resource.willBeRetrieved(originator)).status:	# resource instance may be changed in this call
						return resCheck
				return res


		# do discovery
		# TODO simplify arguments
		if not (res := self.discoverResources(id, originator, request.args.handling, request.args.fo, request.args.conditions, request.args.attributes, permission=permission)).status:	# not found?
			return res.errorResult()				

		# check and filter by ACP. After this allowedResources only contains the resources that are allowed
		allowedResources = []
		for r in cast(List[Resource], res.data):
			if CSE.security.hasAccess(originator, r, permission):
				if not r.willBeRetrieved(originator).status:	# resource instance may be changed in this call
					continue
				allowedResources.append(r)


		#
		#	Handle more sophisticated RCN
		#

		if request.args.rcn == RCN.attributesAndChildResources:
			self.resourceTreeDict(allowedResources, resource)	# the function call add attributes to the target resource
			return Result(status=True, rsc=RC.OK, resource=resource)

		elif request.args.rcn == RCN.attributesAndChildResourceReferences:
			self._resourceTreeReferences(allowedResources, resource, request.args.drt, 'ch')	# the function call add attributes to the target resource
			return Result(status=True, rsc=RC.OK, resource=resource)

		elif request.args.rcn == RCN.childResourceReferences: 
			#childResourcesRef:JSON = { resource.tpe: {} }  # Root resource with no attribute
			#childResourcesRef = self._resourceTreeReferences(allowedResources,  None, request.args.drt, 'm2m:rrl')
			# self._resourceTreeReferences(allowedResources, childResourcesRef[resource.tpe], request.args.drt, 'm2m:rrl')
			childResourcesRef = self._resourceTreeReferences(allowedResources, None, request.args.drt, 'm2m:rrl')
			return Result(status=True, rsc=RC.OK, resource=childResourcesRef)

		elif request.args.rcn == RCN.childResources:
			childResources:JSON = { resource.tpe : {} } #  Root resource as a dict with no attribute
			self.resourceTreeDict(allowedResources, childResources[resource.tpe]) # Adding just child resources
			return Result(status=True, rsc=RC.OK, resource=childResources)

		elif request.args.rcn == RCN.discoveryResultReferences: # URIList
			return Result(status=True, rsc=RC.OK, resource=self._resourcesToURIList(allowedResources, request.args.drt))

		else:
			return Result(status=False, rsc=RC.badRequest, dbg='wrong rcn for RETRIEVE')


	def retrieveResource(self, id:str, originator:str=None, request:CSERequest=None) -> Result:
		"""	If the ID is in SP-relative format then first check whether this is for the
			local CSE. 
			If yes, then adjust the ID and try to retrieve it. 
			If no, then try to retrieve the resource from a connected (!) remote CSE.
		"""
		if id:
			if id.startswith(CSE.cseCsiSlash) and len(id) > self.csiSlashLen:		# TODO for all operations?
				id = id[self.csiSlashLen:]
			else:
				if Utils.isSPRelative(id):
					return CSE.remote.retrieveRemoteResource(id, originator)
		return self.retrieveLocalResource(srn=id, originator=originator, request=request) if Utils.isStructured(id) else self.retrieveLocalResource(ri=id, originator=originator, request=request)


	def retrieveLocalResource(self, ri:str=None, srn:str=None, originator:str=None, request:CSERequest=None) -> Result:
		L.isDebug and L.logDebug(f'Retrieve resource: {ri}|{srn} for originator: {originator if originator else "<internal>"}')

		if ri:
			result = CSE.storage.retrieveResource(ri=ri)		# retrieve via normal ID
		elif srn:
			result = CSE.storage.retrieveResource(srn=srn) 	# retrieve via srn. Try to retrieve by srn (cases of ACPs created for AE and CSR by default)
		else:
			return Result(status=False, rsc=RC.notFound, dbg='resource not found')

		if resource := result.resource:	# Resource found
			# Check for virtual resource
			if resource.ty not in [T.GRP_FOPT, T.PCH_PCU] and Utils.isVirtualResource(resource): # fopt, PCU are handled elsewhere
				return resource.handleRetrieveRequest(request=request, originator=originator)	# type: ignore[no-any-return]
			return result
		# error
		L.isDebug and L.logDebug(f'{result.dbg}: ri:{ri} srn:{srn}')
		return result


	#########################################################################
	#
	#	Discover Resources
	#

	def discoverResources(self, id:str, originator:str, handling:Conditions={}, fo:int=1, conditions:Conditions=None, attributes:Parameters=None, rootResource:Resource=None, permission:Permission=Permission.DISCOVERY) -> Result:
		L.isDebug and L.logDebug('Discovering resources')

		if not rootResource:
			if not (res := self.retrieveResource(id)).resource:
				return Result(status=False, rsc=RC.notFound, dbg=res.dbg)
			rootResource = res.resource

		# get all direct children
		dcrs = self.directChildResources(id)

		# Slice the page (offset and limit)
		offset = handling['ofst'] if 'ofst' in handling else 1			# default: 1 (first resource
		limit = handling['lim'] if 'lim' in handling else sys.maxsize	# default: system max size or "maxint"
		dcrs = dcrs[offset-1:offset-1+limit]							# now dcrs only contains the desired child resources for ofst and lim

		# Get level
		level = handling['lvl'] if 'lvl' in handling else sys.maxsize	# default: system max size or "maxint"

		# a bit of optimization. This length stays the same.
		allLen = len(attributes) if attributes else 0
		if conditions:
			allLen += ( len(conditions) +
			  (len(conditions.get('ty'))-1 if 'ty' in conditions else 0) +		# -1 : compensate for len(conditions) in line 1
			  (len(conditions.get('cty'))-1 if 'cty' in conditions else 0) +		# -1 : compensate for len(conditions) in line 1 
			  (len(conditions.get('lbl'))-1 if 'lbl' in conditions else 0) 		# -1 : compensate for len(conditions) in line 1 
			)

		# Discover the resources
		discoveredResources = self._discoverResources(rootResource, originator, level, fo, allLen, dcrs=dcrs, conditions=conditions, attributes=attributes, permission=permission)

		# NOTE: this list contains all results in the order they could be found while
		#		walking the resource tree.
		#		DON'T CHANGE THE ORDER. DON'T SORT.
		#		Because otherwise the tree cannot be correctly re-constructed otherwise

		# Apply ARP if provided
		if 'arp' in handling:
			arp = handling['arp']
			result = []
			for resource in discoveredResources:
				# Check existence and permissions for the .../{arp} resource
				srn = f'{resource[Resource._srn]}/{arp}'
				if (res := self.retrieveResource(srn)).resource and CSE.security.hasAccess(originator, res.resource, permission):
					result.append(res.resource)
			discoveredResources = result	# re-assign the new resources to discoveredResources

		return Result(status=True, data=discoveredResources)


	def _discoverResources(self, rootResource:Resource, originator:str, level:int, fo:int, allLen:int, dcrs:list[Resource]=None, conditions:Conditions=None, attributes:Parameters=None, permission:Permission=Permission.DISCOVERY) -> list[Resource]:
		if not rootResource or level == 0:		# no resource or level == 0
			return []

		# get all direct children, if not provided
		if not dcrs:
			if len(dcrs := self.directChildResources(rootResource.ri)) == 0:
				return []

		# Filter and add those left to the result
		discoveredResources = []
		for r in dcrs:

			# Exclude virtual resources
			if Utils.isVirtualResource(r):
				continue

			# check permissions and filter. Only then add a resource
			# First match then access. bc if no match then we don't need to check permissions (with all the overhead)
			if self._matchResource(r, conditions, attributes, fo, allLen) and CSE.security.hasAccess(originator, r, permission):
				discoveredResources.append(r)

			# Iterate recursively over all (not only the filtered) direct child resources
			discoveredResources.extend(self._discoverResources(r, originator, level-1, fo, allLen, conditions=conditions, attributes=attributes))

		return discoveredResources


	def _matchResource(self, r:Resource, conditions:Conditions, attributes:Parameters, fo:int, allLen:int) -> bool:	
		""" Match a filter to a resource. """

		# TODO: Implement a couple of optimizations. Can we determine earlier that a match will fail?

		ty = r.ty

		# get the parent resource
		#
		#	TODO when determines how the parentAttribute is actually encoded
		#
		# pr = None
		# if (pi := r.get('pi')) is not None:
		# 	pr = storage.retrieveResource(ri=pi)

		# The matching works like this: go through all the conditions, compare them, and
		# increment 'found' when matching. For fo=AND found must equal all conditions.
		# For fo=OR found must be > 0.
		found = 0

		# check conditions
		if conditions:

			# Types
			# Multiple occurences of ty is always OR'ed. Therefore we add the count of
			# ty's to found (to indicate that the whole set matches)
			if tys := conditions.get('ty'):
				found += len(tys) if ty in tys or str(ty) in tys else 0	# TODO simplify after refactoring requests. ty should only be an int
			if ct := r.ct:
				found += 1 if (c_crb := conditions.get('crb')) and (ct < c_crb) else 0
				found += 1 if (c_cra := conditions.get('cra')) and (ct > c_cra) else 0

			if lt := r.lt:
				found += 1 if (c_ms := conditions.get('ms')) and (lt > c_ms) else 0
				found += 1 if (c_us := conditions.get('us')) and (lt < c_us) else 0

			if (st := r.st) is not None:	# st is an int
				found += 1 if (c_sts := conditions.get('sts')) is not None and (st > c_sts) else 0	# st is an int
				found += 1 if (c_stb := conditions.get('stb')) is not None and (st < c_stb) else 0

			if et := r.et:
				found += 1 if (c_exb := conditions.get('exb')) and (et < c_exb) else 0
				found += 1 if (c_exa := conditions.get('exa')) and (et > c_exa) else 0

			# Check labels similar to types
			rlbl = r.lbl
			if rlbl and (lbls := conditions.get('lbl')):
				for l in lbls:	# TODO list comprehension
					if l in rlbl:
						found += len(lbls)
						break

			if ty in [ T.CIN, T.FCNT ]:	# special handling for CIN, FCNT
				if (cs := r.cs) is not None:	# cs is an int
					found += 1 if (sza := conditions.get('sza')) is not None and (int(cs) >= int(sza)) else 0	# sizes ares ints
					found += 1 if (szb := conditions.get('szb')) is not None and (int(cs) < int(szb)) else 0

			# ContentFormats
			# Multiple occurences of cnf is always OR'ed. Therefore we add the count of
			# cnf's to found (to indicate that the whole set matches)
			# Similar to types.
			if ty in [ T.CIN ]:	# special handling for CIN
				if cnfs := conditions.get('cty'):
					found += len(cnfs) if r.cnf in cnfs else 0

		# TODO childLabels
		# TODO parentLabels
		# TODO childResourceType
		# TODO parentResourceType


		# Attributes:
		if attributes:
			for name in attributes:
				val = attributes[name]
				if isinstance(val, str) and '*' in val:
					found += 1 if (rval := r[name]) is not None and TextTools.simpleMatch(str(rval), val) else 0
				else:
					found += 1 if (rval := r[name]) is not None and str(val) == str(rval) else 0

		# TODO childAttribute
		# TODO parentAttribute


		# Test whether the OR or AND criteria is fullfilled
		if not ((fo == FilterOperation.OR  and found > 0) or 		# OR and found something
				(fo == FilterOperation.AND and allLen == found)		# AND and found everything
			   ): 
			return False

		return True


	#########################################################################
	#
	#	Add resources
	#

	def processCreateRequest(self, request:CSERequest, originator:str, id:str = None) -> Result:
		fopsrn, id = self._checkHybridID(request, id) # overwrite id if another is given
		if not id:
			id = request.id

		# Handle operation execution time and check request expiration
		self._handleOperationExecutionTime(request)
		if not (res := self._checkRequestExpiration(request)).status:
			return res

		# handle fanout point requests
		if (fanoutPointResource := Utils.fanoutPointResource(fopsrn)) and fanoutPointResource.ty == T.GRP_FOPT:
			L.isDebug and L.logDebug(f'Redirecting request to fanout point: {fanoutPointResource.__srn__}')
			return fanoutPointResource.handleCreateRequest(request, fopsrn, request.headers.originator)

		if (ty := request.headers.resourceType) is None:	# Check for type parameter in request, integer
			L.logDebug(dbg := 'type parameter missing in CREATE request')
			return Result(status = False, rsc = RC.badRequest, dbg = dbg)

		# Some Resources are not allowed to be created in a request, return immediately
		if ty in [ T.CSEBase, T.REQ, T.FCI ]:	# TODO: move to constants
			return Result(status=False, rsc=RC.operationNotAllowed, dbg=f'CREATE not allowed for type: {ty}')

		# Get parent resource and check permissions
		if not (res := CSE.dispatcher.retrieveResource(id)).resource:
			L.logWarn(dbg := f'Parent/target resource: {id} not found')
			return Result(status = False, rsc = RC.notFound, dbg = dbg)
		parentResource = res.resource

		if CSE.security.hasAccess(originator, parentResource, Permission.CREATE, ty=ty, isCreateRequest=True, parentResource=parentResource) == False:
			if ty == T.AE:
				return Result(status=False, rsc=RC.securityAssociationRequired, dbg='security association required')
			else:
				return Result(status=False, rsc=RC.originatorHasNoPrivilege, dbg='originator has no privileges')

		# Check for virtual resource
		if Utils.isVirtualResource(parentResource):
			return parentResource.handleCreateRequest(request, id, originator)	# type: ignore[no-any-return]

		# Create resource from the dictionary
		if not (nres := Factory.resourceFromDict(deepcopy(request.pc), pi=parentResource.ri, ty=ty)).resource:	# something wrong, perhaps wrong type
			return Result(status=False, rsc=RC.badRequest, dbg=nres.dbg)
		nresource = nres.resource

		# Check whether the parent allows the adding
		if not (res := parentResource.childWillBeAdded(nresource, originator)).status:
			return res.errorResult()

		# Check resource creation
		if not (rres := CSE.registration.checkResourceCreation(nresource, originator, parentResource)).status:
			return rres.errorResult()

		# check whether the resource already exists, either via ri or srn
		# hasResource() may actually perform the test in one call, but we want to give a distinguished debug message
		if CSE.storage.hasResource(ri=nresource.ri):
			L.logWarn(dbg := f'Resource with ri: {nresource.ri} already exists')
			return Result(status=False, rsc=RC.conflict, dbg=dbg)
		if CSE.storage.hasResource(srn=nresource.__srn__):
			L.logWarn(dbg := f'Resource with structured id: {nresource.__srn__} already exists')
			return Result(status=False, rsc=RC.conflict, dbg=dbg)

		# originator might have changed during this check. Result.data contains this new originator
		originator = cast(str, rres.data) 					
		request.headers.originator = originator	

		# Create the resource. If this fails we deregister everything
		if not (res := CSE.dispatcher.createResource(nresource, parentResource, originator)).resource:
			CSE.registration.checkResourceDeletion(nresource) # deregister resource. Ignore result, we take this from the creation
			return res

		#
		# Handle RCN's
		#

		tpe = res.resource.tpe
		if request.args.rcn is None or request.args.rcn == RCN.attributes:	# Just the resource & attributes, integer
			return res
		elif request.args.rcn == RCN.modifiedAttributes:
			dictOrg = request.pc[tpe]
			dictNew = res.resource.asDict()[tpe]
			return Result(status=res.status, resource={ tpe : Utils.resourceModifiedAttributes(dictOrg, dictNew, request.pc[tpe]) }, rsc=res.rsc, dbg=res.dbg)
		elif request.args.rcn == RCN.hierarchicalAddress:
			return Result(status=res.status, resource={ 'm2m:uri' : Utils.structuredPath(res.resource) }, rsc=res.rsc, dbg=res.dbg)
		elif request.args.rcn == RCN.hierarchicalAddressAttributes:
			return Result(status=res.status, resource={ 'm2m:rce' : { Utils.noNamespace(tpe) : res.resource.asDict()[tpe], 'uri' : Utils.structuredPath(res.resource) }}, rsc=res.rsc, dbg=res.dbg)
		elif request.args.rcn == RCN.nothing:
			return Result(status=res.status, rsc=res.rsc, dbg=res.dbg)
		else:
			return Result(status=False, rsc=RC.badRequest, dbg='wrong rcn for CREATE')
		# TODO C.rcnDiscoveryResultReferences 


	def createResource(self, resource:Resource, parentResource:Resource=None, originator:str=None) -> Result:
		L.isDebug and L.logDebug(f'Adding resource ri: {resource.ri}, type: {resource.ty}')

		if parentResource:
			L.isDebug and L.logDebug(f'Parent ri: {parentResource.ri}')
			if not parentResource.canHaveChild(resource):
				if resource.ty == T.SUB:
					L.logWarn(dbg := 'Parent resource is not subscribable')
					return Result(status=False, rsc=RC.targetNotSubscribable, dbg=dbg)
				else:
					L.logWarn(dbg := f'Invalid child resource type: {T(resource.ty).value}')
					return Result(status=False, rsc=RC.invalidChildResourceType, dbg=dbg)

		# if not already set: determine and add the srn
		if not resource.__srn__:
			resource[resource._srn] = Utils.structuredPath(resource)

		# add the resource to storage
		if not (res := resource.dbCreate(overwrite=False)).status:
			return res

		# Activate the resource
		# This is done *after* writing it to the DB, because in activate the resource might create or access other
		# resources that will try to read the resource from the DB.
		if not (res := resource.activate(parentResource, originator)).status: 	# activate the new resource
			resource.dbDelete()
			return res.errorResult()
		
		# Could be that we changed the resource in the activate, therefore write it again
		if not (res := resource.dbUpdate()).resource:
			resource.dbDelete()
			return res

		# send a create event
		CSE.event.createResource(resource)	# type: ignore

		if parentResource:
			parentResource = parentResource.dbReload().resource		# Read the resource again in case it was updated in the DB
			if not parentResource:
				L.logWarn(dbg := 'Parent resource not found. Probably removed in between?')
				self.deleteResource(resource)
				return Result(status=False, rsc=RC.internalServerError, dbg=dbg)
			parentResource.childAdded(resource, originator)			# notify the parent resource

		return Result(status=True, resource=resource, rsc=RC.created) 	# everything is fine. resource created.


	#########################################################################
	#
	#	Update resources
	#

	def processUpdateRequest(self, request:CSERequest, originator:str, id:str=None) -> Result: 
		fopsrn, id = self._checkHybridID(request, id) # overwrite id if another is given

		# Handle operation execution time and check request expiration
		self._handleOperationExecutionTime(request)
		if not (res := self._checkRequestExpiration(request)).status:
			return res

		# handle fanout point requests
		if (fanoutPointResource := Utils.fanoutPointResource(fopsrn)) and fanoutPointResource.ty == T.GRP_FOPT:
			L.isDebug and L.logDebug(f'Redirecting request to fanout point: {fanoutPointResource.__srn__}')
			return fanoutPointResource.handleUpdateRequest(request, fopsrn, request.headers.originator)

		# Get resource to update
		if not (res := self.retrieveResource(id)).resource:
			L.isWarn and L.logWarn(f'Resource not found: {res.dbg}')
			return Result(status=False, rsc=RC.notFound, dbg=res.dbg)
		resource = res.resource
		if resource.readOnly:
			return Result(status=False, rsc=RC.operationNotAllowed, dbg='resource is read-only')

		# Some Resources are not allowed to be updated in a request, return immediately
		if resource.ty in [ T.CIN, T.FCI, T.TSI ]:		# TODO: move to constants
			return Result(status=False, rsc=RC.operationNotAllowed, dbg=f'UPDATE not allowed for type: {resource.ty}')

		#
		#	Permission check
		#	If this is an 'acpi' update?

		if not (res := CSE.security.hasAcpiUpdatePermission(request, resource, originator)).status:
			return res
		if not res.data:	# data == None or False indicates that this is NOT an ACPI update. In this case we need a normal permission check
			if CSE.security.hasAccess(originator, resource, Permission.UPDATE) == False:
				return Result(status=False, rsc=RC.originatorHasNoPrivilege, dbg='originator has no privileges')

		# Check for virtual resource
		if Utils.isVirtualResource(resource):
			return resource.handleUpdateRequest(request, id, originator)	# type: ignore[no-any-return]

		dictOrg = deepcopy(resource.dict)	# Save for later


		if not (res := self.updateResource(resource, deepcopy(request.pc), originator=originator)).resource:
			return res.errorResult()
		resource = res.resource 	# re-assign resource (might have been changed during update)

		# Check resource update with registration
		if not (rres := CSE.registration.checkResourceUpdate(resource, deepcopy(request.pc))).status:
			return rres.errorResult()

		#
		# Handle RCN's
		#

		tpe = resource.tpe
		if request.args.rcn is None or request.args.rcn == RCN.attributes:	# rcn is an int
			return res
		elif request.args.rcn == RCN.modifiedAttributes:
			dictNew = deepcopy(resource.dict)
			requestPC = request.pc[tpe]
			# return only the modified attributes. This does only include those attributes that are updated differently, or are
			# changed by the CSE, then from the original request. Luckily, all key/values that are touched in the update request
			#  are in the resource's __modified__ variable.
			return Result(status=res.status, resource={ tpe : Utils.resourceModifiedAttributes(dictOrg, dictNew, requestPC, modifiers=resource[Resource._modified]) }, rsc=res.rsc)
		elif request.args.rcn == RCN.nothing:
			return Result(status=res.status, rsc=res.rsc)
		# TODO C.rcnDiscoveryResultReferences 
		else:
			return Result(status=False, rsc=RC.badRequest, dbg='wrong rcn for UPDATE')


	def updateResource(self, resource:Resource, dct:JSON=None, doUpdateCheck:bool=True, originator:str=None) -> Result:
		L.isDebug and L.logDebug(f'Updating resource ri: {resource.ri}, type: {resource.ty}')
		if doUpdateCheck:
			if not (res := resource.update(dct, originator)).status:
				return res.errorResult()
		else:
			L.isDebug and L.logDebug('No check, skipping resource update')

		# send a create event
		CSE.event.updateResource(resource)		# type: ignore
		return resource.dbUpdate()


	#########################################################################
	#
	#	Remove resources
	#

	def processDeleteRequest(self, request:CSERequest, originator:str, id:str=None) -> Result:
		fopsrn, id = self._checkHybridID(request, id) # overwrite id if another is given

		# Handle operation execution time and check request expiration
		self._handleOperationExecutionTime(request)
		if not (res := self._checkRequestExpiration(request)).status:
			return res

		# handle fanout point requests
		if (fanoutPointResource := Utils.fanoutPointResource(fopsrn)) and fanoutPointResource.ty == T.GRP_FOPT:
			L.isDebug and L.logDebug(f'Redirecting request to fanout point: {fanoutPointResource.__srn__}')
			return fanoutPointResource.handleDeleteRequest(request, fopsrn, request.headers.originator)

		# get resource to be removed and check permissions
		if not (res := self.retrieveResource(id)).resource:
			L.isDebug and L.logDebug(res.dbg)
			return Result(status=False, rsc=RC.notFound, dbg=res.dbg)
		resource = res.resource

		if CSE.security.hasAccess(originator, resource, Permission.DELETE) == False:
			return Result(status=False, rsc=RC.originatorHasNoPrivilege, dbg='originator has no privileges')

		# Check for virtual resource
		if Utils.isVirtualResource(resource):
			return resource.handleDeleteRequest(request, id, originator)	# type: ignore[no-any-return]

		#
		# Handle RCN's first. Afterward the resource & children are no more
		#

		tpe = resource.tpe
		result: Any = None
		if request.args.rcn is None or request.args.rcn == RCN.nothing:	# rcn is an int
			result = None
		elif request.args.rcn == RCN.attributes:
			result = resource
		# resource and child resources, full attributes
		elif request.args.rcn == RCN.attributesAndChildResources:
			children = self.discoverChildren(id, resource, originator, request.args.handling, Permission.DELETE)
			self._childResourceTree(children, resource)	# the function call add attributes to the result resource. Don't use the return value directly
			result = resource
		# direct child resources, NOT the root resource
		elif request.args.rcn == RCN.childResources:
			children = self.discoverChildren(id, resource, originator, request.args.handling, Permission.DELETE)
			childResources:JSON = { resource.tpe : {} }			# Root resource as a dict with no attributes
			self.resourceTreeDict(children, childResources[resource.tpe])
			result = childResources
		elif request.args.rcn == RCN.attributesAndChildResourceReferences:
			children = self.discoverChildren(id, resource, originator, request.args.handling, Permission.DELETE)
			self._resourceTreeReferences(children, resource, request.args.drt, 'ch')	# the function call add attributes to the result resource
			result = resource
		elif request.args.rcn == RCN.childResourceReferences: # child resource references
			children = self.discoverChildren(id, resource, originator, request.args.handling, Permission.DELETE)
			childResourcesRef:JSON = { resource.tpe: {} }  # Root resource with no attribute
			self._resourceTreeReferences(children, childResourcesRef[resource.tpe], request.args.drt, 'm2m:rrl')
			result = childResourcesRef
		# TODO RCN.discoveryResultReferences
		else:
			return Result(status=False, rsc=RC.badRequest, dbg='wrong rcn for DELETE')

		# remove resource
		res = self.deleteResource(resource, originator, withDeregistration=True)
		return Result(status=res.status, resource=result, rsc=res.rsc, dbg=res.dbg)


	def deleteResource(self, resource:Resource, originator:str=None, withDeregistration:bool=False, parentResource:Resource=None, doDeleteCheck:bool=True) -> Result:
		L.isDebug and L.logDebug(f'Removing resource ri: {resource.ri}, type: {resource.ty}')

		resource.deactivate(originator)	# deactivate it first

		# Check resource deletion
		if withDeregistration:
			if not (res := CSE.registration.checkResourceDeletion(resource)).status:
				return Result(status=False, rsc=RC.badRequest, dbg=res.dbg)

		# Retrieve the parent resource now, because we need it later
		if not parentResource:
			parentResource = resource.retrieveParentResource()

		# delete the resource from the DB. Save the result to return later
		res = resource.dbDelete()

		# send a delete event
		CSE.event.deleteResource(resource) 	# type: ignore

		# Now notify the parent resource
		if doDeleteCheck and parentResource:
			parentResource.childRemoved(resource, originator)

		return Result(status=res.status, resource=resource, rsc=res.rsc, dbg=res.dbg)


	#########################################################################
	#
	#	Notify
	#

	def processNotifyRequest(self, request:CSERequest, originator:str, id:str=None) -> Result:
		srn, id = self._checkHybridID(request, id) # overwrite id if another is given

		# Handle operation execution time and check request expiration
		self._handleOperationExecutionTime(request)
		if not (res := self._checkRequestExpiration(request)).status:
			return res

		# get resource to be notified and check permissions
		if not (res := self.retrieveResource(id)).resource:
			L.isDebug and L.logDebug(res.dbg)
			return Result(status=False, rsc=RC.notFound, dbg=res.dbg)
		targetResource = res.resource

		# Security checks below

		
		# Check for <pollingChannelURI> resource
		# This is also the only resource type supported that can receive notifications, yet
		if targetResource.ty == T.PCH_PCU :
			if not CSE.security.hasAccessToPollingChannel(originator, targetResource):
				L.logDebug(dbg:=f'Originator: {originator} has not access to <pollingChannelURI>: {id}')
				return Result(status=False, rsc=RC.originatorHasNoPrivilege, dbg=dbg)
			return targetResource.handleNotifyRequest(request, originator)	# type: ignore[no-any-return]

		if targetResource.ty in [ T.AE, T.CSR, T.CSEBase ]:
			if not CSE.security.hasAccess(originator, targetResource, Permission.NOTIFY):
				L.logDebug(dbg := f'Originator has no NOTIFY privilege for: {id}')
				return Result(status = False, rsc = RC.originatorHasNoPrivilege, dbg = dbg)
			#  A Notification to one of these resources will always be a R
			return CSE.request.handleReceivedNotifyRequest(id, request = request, originator = originator)

		# error
		L.logDebug(dbg := f'Unsupported resource type: {targetResource.ty} for notifications. Supported: <PCU>.')
		return Result(status = False, rsc = RC.badRequest, dbg = dbg)



	#########################################################################
	#
	#	Public Utility methods
	#

	def directChildResources(self, pi:str, ty:T=None) -> list[Resource]:
		"""	Return all child resources of a resource, optionally filtered by type.
			An empty list is returned if no child resource could be found.
		"""
		return CSE.storage.directChildResources(pi, ty)


	def countDirectChildResources(self, pi:str, ty:T=None) -> int:
		"""	Return the number of all child resources of resource, optionally filtered by type. 
		"""
		return CSE.storage.countDirectChildResources(pi, ty)


	def discoverChildren(self, id:str, resource:Resource, originator:str, handling:JSON, permission:Permission) -> list[Resource]:
		# TODO documentation
		if not (res := self.discoverResources(id, originator, handling, rootResource=resource, permission=permission)).status:
			return None
		# check and filter by ACP
		children = []
		for r in cast(List[Resource], res.data):
			if CSE.security.hasAccess(originator, r, permission):
				children.append(r)
		return children


	def countResources(self, ty:T|Tuple[T, ...]=None) -> int:
		""" Return total number of resources.
			Optional filter by type.
		"""

		# Count all resources
		if ty is None:	# ty is an int
			return CSE.storage.countResources()
		
		# Count all resources of the given types
		if isinstance(ty, tuple):
			cnt = 0
			for t in ty:
				cnt += len(CSE.storage.retrieveResourcesByType(t))
			return cnt

		# Count all resources of a specific type
		return len(CSE.storage.retrieveResourcesByType(ty))


	def retrieveResourcesByType(self, ty:T) -> list[Resource]:
		""" Retrieve all resources of a type. 
		"""
		result = []
		rss = CSE.storage.retrieveResourcesByType(ty)
		for rs in (rss or []):
			result.append(Factory.resourceFromDict(rs).resource)
		return result
	

	def deleteChildResources(self, parentResource:Resource, originator:str, ty:T=None) -> None:
		"""	Remove all child resources of a parent recursively. 
			If `ty` is set only the resources of this type are removed.
		"""
		# Remove directChildResources
		rs = self.directChildResources(parentResource.ri)
		for r in rs:
			if ty is None or r.ty == ty:	# ty is an int
				parentResource.childRemoved(r, originator)	# recursion here
				self.deleteResource(r, originator, parentResource=parentResource)


	#########################################################################
	#
	#	Request execution utilities
	#

	def _handleOperationExecutionTime(self, request:CSERequest) -> None:
		"""	Handle operation execution time and request expiration.
		"""
		if request.headers.operationExecutionTime:
			delay = DateUtils.timeUntilAbsRelTimestamp(request.headers.operationExecutionTime)
			L.isDebug and L.logDebug(f'Waiting: {delay:.4f} seconds until delayed execution')
			DateUtils.waitFor(delay)	# Just wait some time


	def _checkRequestExpiration(self, request:CSERequest) -> Result:
		"""	Check request expiration timeout. Returns a negative Result when the timeout
			timestamp has been reached or passed.
		"""
		if request.headers._retUTCts is not None and DateUtils.timeUntilTimestamp(request.headers._retUTCts) <= 0.0:
			L.logDebug(dbg := 'Request timed out')
			return Result(status=False, rsc=RC.requestTimeout, dbg=dbg)
		return Result(status=True)



	#########################################################################
	#
	#	Internal methods for collecting resources and child resources into structures
	#

	def _resourcesToURIList(self, resources:list[Resource], drt:int) -> JSON:
		"""	Create a m2m:uril structure from a list of resources.
		"""
		cseid = f'{CSE.cseCsi}/'	# SP relative. csi already starts with a "/"
		lst = []
		for r in resources:
			lst.append(Utils.structuredPath(r) if drt == DRT.structured else cseid + r.ri)
		return { 'm2m:uril' : lst }


	def resourceTreeDict(self, resources:list[Resource], targetResource:Resource|JSON) -> list[Resource]:
		"""	Recursively walk the results and build a sub-resource tree for each resource type.
		"""
		rri = targetResource['ri'] if 'ri' in targetResource else None
		while True:		# go multiple times per level through the resources until the list is empty
			result = []
			handledTy = None
			handledTPE = None
			idx = 0
			while idx < len(resources):
				r = resources[idx]

				if rri and r.pi != rri:	# only direct children
					idx += 1
					continue
				if T.isVirtualResource(r.ty):	# Skip latest, oldest etc virtual resources
					idx += 1
					continue
				if handledTy is None:					# ty is an int
					handledTy = r.ty					# this round we check this type
					handledTPE = r.tpe					# ... and this TPE (important to distinguish specializations in mgmtObj and fcnt )
				if r.ty == handledTy and r.tpe == handledTPE:		# handle only resources of the currently handled type and TPE!
					result.append(r)					# append the found resource 
					resources.remove(r)						# remove resource from the original list (greedy), but don't increment the idx
					resources = self.resourceTreeDict(resources, r)	# check recursively whether this resource has children
				else:
					idx += 1							# next resource

			# add all found resources under the same type tag to the rootResource
			if len(result) > 0:
				# sort resources by type and then by lowercase rn
				if self.sortDiscoveryResources:
					# result.sort(key=lambda x:(x.ty, x.rn.lower()))
					result.sort(key=lambda x: (x.ty, x.ct) if x.ty in [ T.CIN, T.FCI, T.TSI ] else (x.ty, x.rn.lower()))
				targetResource[result[0].tpe] = [r.asDict(embedded=False) for r in result]
				# TODO not all child resources are lists [...] Handle just to-1 relations
			else:
				break # end of list, leave while loop
		return resources # Return the remaining list


	def _resourceTreeReferences(self, resources:list[Resource], targetResource:Resource|JSON, drt:DRT=DRT.structured, tp:str='m2m:rrl') -> Resource|JSON:
		""" Retrieve child resource references of a resource and add them to
			a new target resource as "children" """
		if not targetResource:
			targetResource = { }

		t = []

		# sort resources by type and then by lowercase rn
		if self.sortDiscoveryResources:
			resources.sort(key=lambda x:(x.ty, x.rn.lower()))
		
		for r in resources:
			if r.ty in [ T.CNT_OL, T.CNT_LA, T.FCNT_OL, T.FCNT_LA ]:	# Skip latest, oldest virtual resources
				continue
			ref = { 'nm' : r['rn'], 'typ' : r['ty'], 'val' :  Utils.structuredPath(r) if drt == DRT.structured else r.ri}
			if r.ty == T.FCNT:
				ref['spty'] = r.cnd		# TODO Is this correct? Actually specializationID in TS-0004 6.3.5.29, but this seems to be wrong
			t.append(ref)

		# The following reflects a current inconsistency in the standard.
		# If this list of childResourceReferences is for rcn=5 (attributesAndChildResourceReferences), then the structure
		# is -> 'ch' : [ <listOfChildResourceRef> ]
		# If this list of childResourceReferences is for rcn=6 (childResourceReferences), then the structure 
		# is -> '{ 'rrl' : { 'rrf' : [ <listOfChildResourceRef> ]}}  ( an extra rrf struture )
		targetResource[tp] = { "rrf" : t } if tp == 'm2m:rrl' else t
		return targetResource


	# Retrieve full child resources of a resource and add them to a new target resource
	def _childResourceTree(self, resources:list[Resource], targetResource:Resource|JSON) -> None:
		if len(resources) == 0:
			return
		result:JSON = {}
		self.resourceTreeDict(resources, result)	# rootResource is filled with the result
		for k,v in result.items():			# copy child resources to result resource
			targetResource[k] = v


	#########################################################################
	#
	#	Internal methods for ID handling
	#

	def _checkHybridID(self, request:CSERequest, id:str) -> Tuple[str, str]:
		"""	Return a corrected ID and SRN in case this is a hybrid ID.
			srn might be None. 
			Returns: (srn, id)
		"""
		if id:
			return Utils.srnFromHybrid(None, id) # Hybrid
		return Utils.srnFromHybrid(request.srn, request.id) # Hybrid

