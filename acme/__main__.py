#
#	acme.py
#
#	(c) 2020 by Andreas Kraft
#	License: BSD 3-Clause License. See the LICENSE file for further details.
#
#	Starter for the ACME CSE
#

"""	This module contains the ACME CSE implementation. It is the main module of the ACME CSE.
"""

import os, re, sys
if sys.version_info < (3, 8):
	print('Python version >= 3.8 is required')
	quit(1)

import argparse
# parent = pathlib.Path(os.path.abspath(os.path.dirname(__file__))).parent
# # sys.path.append(f'{parent}/acme')
try:
	from .etc.Constants import Constants as C
	from .services import CSE as CSE
	from rich.console import Console
except ImportError as e:
	# Raise the exception when in debug mode
	if 'ACME_DEBUG' in os.environ:
		raise e
	
	match e.msg:
		# Give hint to run ACME as a module
		case x if 'attempted relative import' in x:
			print(f'\nPlease run acme as a package:\n\n\t{sys.executable} -m {sys.argv[0]} [arguments]\n')
	
		# Give hint how to do the installation
		case x if 'No module named' in x:
			m = re.search("'(.+?)'", e.msg)
			package = f' ({m.group(1)}) ' if m else ' '
			print(f'\nOne or more required packages or modules{package}could not be found.\nPlease install the missing packages, e.g. by running the following command:\n\n\t{sys.executable} -m pip install -r requirements.txt\n')

			# Ask if the user wants to install the missing packages
			try:
				if input('\nDo you want to install the missing packages now? [y/N] ') in ['y', 'Y']:
					import os
					os.system(f'{sys.executable} -m pip install -r requirements.txt')

					# Ask if the user wants to start ACME
					if input('\nDo you want to start ACME now? [Y/n] ') in ['y', 'Y', '']:
						os.system(f'{sys.executable} -m acme {" ".join(sys.argv[1:])}')

			except Exception as e2:
				print(f'\nError during installation: {e2}\n')
		
		case _:
			print(f'\nError during import: {e.msg}\n')

	quit(1)


# Handle command line arguments
def parseArgs() -> argparse.Namespace:
	""" Parse the command line arguments.
	
		Returns:
			The parsed arguments.
	"""
	parser = argparse.ArgumentParser(prog='acme')
	parser.add_argument('--config', action='store', dest='configfile', default=C.defaultUserConfigFile, metavar='<filename>', help='specify the configuration file')

	# two mutual exlcusive arguments
	groupEnableHttp = parser.add_mutually_exclusive_group()
	groupEnableHttp.add_argument('--http', action='store_false', dest='http', default=None, help='run CSE with http server')
	groupEnableHttp.add_argument('--https', action='store_true', dest='https', default=None, help='run CSE with https server')
	groupEnableHttp.add_argument('--http-wsgi', action='store_true', dest='httpWsgi', default=None, help='run CSE with http WSGI support')

	groupEnableMqtt = parser.add_mutually_exclusive_group()
	groupEnableMqtt.add_argument('--mqtt', action='store_true', dest='mqttenabled', default=None, help='enable mqtt binding')
	groupEnableMqtt.add_argument('--no-mqtt', action='store_false', dest='mqttenabled', default=None, help='disable mqtt binding')

	groupRemoteCSE = parser.add_mutually_exclusive_group()
	groupRemoteCSE.add_argument('--remote-cse', action='store_true', dest='remotecseenabled', default=None, help='enable remote CSE connections')
	groupRemoteCSE.add_argument('--no-remote-cse', action='store_false', dest='remotecseenabled', default=None, help='disable remote CSE connections')

	groupEnableStats = parser.add_mutually_exclusive_group()
	groupEnableStats.add_argument('--statistics', action='store_true', dest='statisticsenabled', default=None, help='enable collecting CSE statistics')
	groupEnableStats.add_argument('--no-statistics', action='store_false', dest='statisticsenabled', default=None, help='disable collecting CSE statistics')

	parser.add_argument('--db-directory', action='store', dest='dbdirectory', metavar='<data-directory>', default=None, help='specify the DB´s data directory')
	parser.add_argument('--db-reset', action='store_true', dest='dbreset', default=None, help='reset the DB when starting the CSE')
	parser.add_argument('--db-storage', action='store', dest='dbstoragemode', default=None, choices=[ 'memory', 'disk' ], type=str.lower, help='specify the DB´s storage mode')
	parser.add_argument('--http-address', action='store', dest='httpaddress', metavar='<server-URL>', help='specify the CSE\'s http server URL')
	parser.add_argument('--http-port', action='store', dest='httpport', metavar='<http-port>',  type=int, help='specify the CSE\'s http port')
	parser.add_argument('--import-directory', action='store', dest='importdirectory', default=None, metavar='<directory>', help='specify the import directory')
	parser.add_argument('--network-interface', action='store', dest='listenif', metavar='<ip-address>', default=None, help='specify the network interface/IP address to bind to')
	parser.add_argument('--log-level', action='store', dest='loglevel', default=None, choices=[ 'info', 'error', 'warn', 'debug', 'off'], type=str.lower, help='set the log level, or turn logging off')
	parser.add_argument('--headless', action='store_true', dest='headless', default=None, help='operate the CSE in headless mode')
	parser.add_argument('--textui', action='store_true', dest='textui', default=None, help='start with the CSE\'s text UI')
	
	return parser.parse_args()



def main() -> None:
	""" Main function of the ACME CSE.
	"""
	#	Start the CSE with command line arguments.
	#	In case the CSE should be started without command line parsing, the values
	#	can be passed instead. Unknown arguments are ignored.
	#	For example:
	#
	#		CSE.startup(None, configfile=defaultConfigFile, loglevel='error', resetdb=None)
	#
	#	Note: Always pass at least 'None' as first and then the 'configfile' parameter.
	Console().print(f'\n{C.textLogo} ' + C.version + ' - [bold]An open source CSE Middleware for Education[/bold]\n\n', highlight = False)
	if CSE.startup(parseArgs()):
		CSE.run()

if __name__ == '__main__':
	main()
