
import os, fnmatch, importlib, time, argparse
from rich.console import Console
from rich.table import Table
from rich.style import Style
import init
from acme.etc.Constants import Constants as C


# TODO testTransferRequests.py

loadTests 	= [ 'testLoad' ]
singleTests = []

def isRunTest(name:str) -> bool:
	if args.runAll:						# run all tests
		return True
	if len(singleTests) > 0:			# run only specified tests
		return name in singleTests
	if args.includeLoadTests:			# include all load tests
		return True
	return (len([ n for n in loadTests if name.startswith(n) ]) > 0) == args.loadTestsOnly


if __name__ == '__main__':
	console       = Console()
	totalErrors   = 0
	totalRunTests = 0
	totalSuites   = 0
	totalSkipped  = 0
	modules       = []
	results       = {}

	def checkPositive(value:str) -> int:
		ivalue = int(value)
		if ivalue <= 0:
			raise argparse.ArgumentTypeError(f'{value} is ivalid. It must be a positive int value')
		return ivalue

	# Parse command line arguments
	parser = argparse.ArgumentParser()
	parser.add_argument('--load-include', action='store_true', dest='includeLoadTests', default=False, help='include load tests in test runs')
	parser.add_argument('--load-only', action='store_true', dest='loadTestsOnly', default=False, help='run only load tests in test runs')
	parser.add_argument('--all', action='store_true', dest='runAll', default=False, help='run all tests')
	parser.add_argument('--show-skipped', action='store_true', dest='showSkipped', default=False, help='show skipped tests in summary')
	parser.add_argument('--verbosity', action='store', dest='verbosity', type=int, choices=[0,1,2], default=2, help='set verbosity (default: 2)')
	parser.add_argument('--verbose-requests', action='store_true', dest='verboseRequests', default=False, help='enable verbose requests and response output')
	parser.add_argument('--run-count', action='store', dest='numberOfRuns', type=checkPositive, default=1, help='run each test module n times (default: 1)')

	groupFail = parser.add_mutually_exclusive_group()
	groupFail.add_argument('--failfast', action='store_true', dest='failFast', default=True, help='Stop tests after failure (default)')
	groupFail.add_argument('--no-failfast', action='store_false', dest='failFast', default=True, help='Continue tests after failure')

	parser.add_argument('tests', nargs='*', help='specific tests to run')
	args = parser.parse_args()

	# Clean optional single tests
	singleTests = [ test if not test.endswith('.py') else test[:-3] for test in args.tests ]


	# Get all filenames with tests and load them as modules
	filenames = fnmatch.filter(os.listdir('.'), 'test*.py')
	filenames.sort()
	init.verboseRequests = args.verboseRequests 

	for name in filenames:
		modules.append(importlib.import_module(name[:-3]))

	# Run the tests and get some measurements
	totalTimeStart		  = time.perf_counter()
	totalProcessTimeStart = time.process_time()
	totalSleepTime		  = 0.0
	init.requestCount	  = 0

	
	for module in modules:
		if hasattr(module, 'run'):
			totalSuites += 1
			name = module.__name__
			if isRunTest(name): 	# exclude / include some tests
				for n in range(args.numberOfRuns):
					if args.numberOfRuns > 1:
						name = f'{module.__name__}_{n}'
					console.print(f'[bright_blue]Running tests from [bold]{name}')
					startProcessTime = time.process_time()
					startPerfTime = time.perf_counter()
					startRequestCount = init.requestCount
					testExecuted, errors, skipped, sleepTimeCount = module.run(testVerbosity = args.verbosity, 
																			   testFailFast = args.failFast)	# type: ignore
					init.stopNotificationServer()

					durationProcess = time.process_time() - startProcessTime
					duration = time.perf_counter() - startPerfTime
					if testExecuted > 0:	# don't count none-run tests
						totalErrors += errors
						totalRunTests += testExecuted
					totalSkipped += skipped
					totalSleepTime += sleepTimeCount
					results[name] = ( testExecuted, errors, duration, durationProcess, skipped, init.requestCount - startRequestCount, sleepTimeCount )
					console.print(f'[spring_green3]Successfully executed tests: {testExecuted}')
					if errors > 0:
						console.print(f'[red]Errors: {errors}')
				else:
					if args.showSkipped:
						results[name] = ( 0, 0, 0, 0, 1, init.requestCount - startRequestCount, 0.0 )

	totalProcessTime	= time.process_time() - totalProcessTimeStart
	totalExecTime 		= time.perf_counter() - totalTimeStart

	# Print Summary
	console.print()
	table = Table(show_header=True, header_style='bright_blue', show_footer=True, footer_style='', title=f'{C.textLogo} - Test Results')
	table.add_column('Test Suite', footer='Totals', no_wrap=True)
	table.add_column('Count', footer=f'[spring_green3]{totalRunTests if totalErrors == 0 else str(totalRunTests)}[/spring_green3]', justify='right')
	table.add_column('Skipped', footer=f'[yellow]{totalSkipped}[/yellow]' if totalSkipped > 0 else '[spring_green3]0[spring_green3]', justify='right')
	table.add_column('Errors', footer=f'[red]{totalErrors}[/red]' if totalErrors > 0 else '[spring_green3]0[spring_green3]', justify='right')
	table.add_column('Times\nExec | Sleep | Proc', footer=f'{totalExecTime:8.4f} | {totalSleepTime:6.2f} | {totalProcessTime:8.4f}', justify='center')
	# table.add_column('Exec Time', footer=f'{totalExecTime:.4f}', justify='right')
	# table.add_column('Sleep Time', footer=f'{totalSleepTime:.2f}' if totalRunTests != 0 else '0.0', justify='right')
	# table.add_column('Proc Time', footer=f'{totalProcessTime:.4f}', justify='right')
	table.add_column('Exec Time per\nTest | Request', footer=f'{totalExecTime/totalRunTests:7.4f} | {totalExecTime/init.requestCount:7.4f}' if totalRunTests != 0 else '000.0000 | 000.0000', justify='center')
	table.add_column('Proc Time per\nTest | Request', footer=f'{totalProcessTime/totalRunTests:7.4f} | {totalProcessTime/init.requestCount:7.4f}' if totalRunTests != 0 else '000.0000 | 000.0000', justify='center')
	table.add_column('Requests', footer=f'{init.requestCount}', justify='right')
	# Styles
	styleDisabled = Style(dim=True)
	styleDisabled2 = Style(dim=True, bgcolor='grey11')
	styleEnabled = Style()
	styleEnabled2 = Style(bgcolor='grey11')
	cnt = 0
	for k,v in results.items():
		cnt += 1
		if cnt%2 == 0:
			style = styleEnabled2 if v[0] > 0 else styleDisabled2
		else:
			style = styleEnabled if v[0] > 0 else styleDisabled
		table.add_row(	k, 
						str(v[0]), 
						f'[yellow]{v[4]}[/yellow]' if v[4] > 0 and v[0] > 0 else str(v[4]),
						f'[red]{v[1]}[/red]' if v[1] > 0 and v[0] > 0 else str(v[1]),
						f'{v[2]:8.4f} | {v[6]:6.2f} | {v[3]:8.4f}' if v[0] > 0 else '', 
						# f'{v[6]:.2f}',
						# f'{v[3]:.4f}' if v[0] > 0 else '',
						f'{(v[2]/v[0]):7.4f} | {(v[2]/v[5]):7.4f}' if v[0] > 0 else '',
						f'{(v[3]/v[0]):7.4f} | {(v[3]/v[5]):7.4f}' if v[0] > 0 else '',
						f'{v[5]}',
						style=style)
	console.print(table)

