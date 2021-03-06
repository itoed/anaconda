# Copyright (C) 2014  Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 2.1 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Author: Chris Lumens <clumens@redhat.com>

from dogtail.utils import doDelay

from . import UITestCase

class BasicRootPasswordTestCase(UITestCase):
    def check_enter_password(self):
        # The warning bar starts off telling us there's no password set.
        self.check_warning_bar("The password is empty")

        entry = self.find("Password", "text")
        self.assertIsNotNone(entry, msg="Password entry should be displayed")
        entry.grabFocus()
        entry.text = "asdfasdf"

        # That wasn't a very good password and we haven't confirmed it, so the
        # bar is still displayed at the bottom.
        doDelay(1)
        self.check_warning_bar("it does not contain enough DIFFERENT characters")

        # Let's confirm that terrible password.
        entry = self.find("Confirm Password", "text")
        self.assertIsNotNone(entry, msg="Confirm password should be displayed")
        entry.grabFocus()
        entry.text = "asdfasdf"

        # But of course it's still a terrible password, so the bar is still
        # displayed at the bottom.
        doDelay(1)
        self.check_warning_bar("it does not contain enough DIFFERENT characters")

    def check_click_done(self):
        # Press the Done button once, which won't take us anywhere but will change the
        # warning label at the bottom.
        self.click_button("_Done")
        self.check_warning_bar("Press Done again")

        # Pressing Done again should take us back to the progress hub.
        self.exit_spoke(hubName="CONFIGURATION")

    def _run(self):
        # First, we need to click on the spoke selector.
        self.enter_spoke("ROOT PASSWORD")

        # Now, verify we are on the right screen.
        self.check_window_displayed("ROOT PASSWORD")

        # And now we can check everything else on the screen.
        self.check_enter_password()
        self.check_click_done()
