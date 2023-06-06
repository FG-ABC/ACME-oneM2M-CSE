#
#	ACMEHeader.py
#
#	(c) 2023 by Andreas Kraft
#	License: BSD 3-Clause License. See the LICENSE file for further details.
#
"""	This module defines the header for the ACME text UI.
"""
from datetime import datetime, timezone

from rich.text import Text
from textual.app import ComposeResult, RenderResult
from textual.widgets import Header, Label
from textual.widgets._header import HeaderClock, HeaderClockSpace, HeaderTitle
from textual.containers import Horizontal

from ..services import CSE
from ..etc.Constants import Constants
from ..etc.DateUtils import toISO8601Date


class ACMEHeaderClock(HeaderClock):
	"""	Display a modified HeaderClock. It shows the time based on UTC to help
	  	with working with oneM2M timestamps, which are all UTC based."""
	
	DEFAULT_CSS = """
ACMEHeaderClock {
	background: transparent;
}

HeaderClockSpace {
	width: 25;
}
"""
	
	def render(self) -> RenderResult:
		"""Render the header clock.

		Returns:
			The rendered clock.
		"""
		return Text(f'{toISO8601Date(datetime.now(tz = timezone.utc), readable = True)[:19]} UTC')	


class ACMEHeaderTitle(HeaderTitle):
	"""	Display the title / subtitle in the header."""

	def render(self) -> Text:
		return Text.from_markup(f'{Constants.textLogo}[dim] {CSE.cseType.name}-CSE : {CSE.cseCsi}', overflow = 'ellipsis')


class ACMEHeader(Header):

	DEFAULT_CSS = '''
ACMEHeader {
	height: 3;
}
'''

	def compose(self) -> ComposeResult:
		self.tall = True
		with Horizontal():
			yield Label(' ' * 13)	# to align the title and the extra space of the clock
			yield ACMEHeaderTitle()
		yield ACMEHeaderClock() if self._show_clock else HeaderClockSpace()

