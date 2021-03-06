#!/usr/bin/python
#
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

__all__ = ["Creator", "OutsideMixin"]

import blivet

from contextlib import contextmanager
import os
import shutil
import subprocess
import tempfile
import unittest
import errno

# Copied from python's subprocess.py
def eintr_retry_call(func, *args):
    """Retry an interruptible system call if interrupted."""
    while True:
        try:
            return func(*args)
        except (OSError, IOError) as e:
            if e.errno == errno.EINTR:
                continue
            raise

class Creator(object):
    """A Creator subclass defines all the parameters for making a VM to run a
       test against as well as handles creating and running that VM, inspecting
       results, and managing temporary data.

       Most Creator subclasses will only need to define the following four
       attributes:

       drives       -- A list of tuples describing disk images to create.  Each
                       tuple is the name and size, in GB.
       environ      -- A dictionary of environment variables that should be added
                       to the environment the test suite will run under.
       name         -- A unique string that names a Creator.  This name will
                       be used in creating the results directory (and perhaps
                       other places in the future) so make sure it doesn't
                       conflict with another object.
       reqMemory    -- The amount of memory this VM needs, in MB.  The default
                       is 2048, which seems to be the minimum required to
                       make things run quickly.  Redefine if you need more.
       tests        -- A list of tuples describing which test cases make up
                       this test.  Each tuple is the name of the module
                       containing the test case (minus the leading "inside."
                       and the name of the test case class.  Tests will be
                       run in the order provided.
    """
    drives = []
    environ = {}
    name = "Creator"
    reqMemory = 2048
    tests = []

    def __init__(self):
        self._drivePaths = {}
        self._mountpoint = None
        self._proc = None
        self._tempdir = None

    def archive(self):
        """Copy all log files and other test results to a subdirectory of the
           given resultsdir.  If logs are no longer available, this method
           does nothing.  It is up to the caller to make sure logs are available
           beforehand and clean up afterwards.
        """
        from testconfig import config

        if not os.path.ismount(self.mountpoint):
            return

        shutil.copytree(self.mountpoint + "/result", config["resultsdir"] + "/" + self.name)

    def cleanup(self):
        """Remove all disk images used during this test case and the temporary
           directory they were stored in.
        """
        shutil.rmtree(self.tempdir, ignore_errors=True)
        os.unlink(self._drivePaths["suite"])

    def die(self):
        """Kill any running qemu process previously started by this test."""
        if self._proc:
            self._proc.kill()
            self._proc = None

    def makeDrives(self):
        """Create all hard drive images associated with this test.  Images
           must be listed in Creator.drives and will be stored in a temporary
           directory this method creates.  It is up to the caller to remove
           everything later by calling Creator.cleanup.
        """
        for (drive, size) in self.drives:
            (fd, diskimage) = tempfile.mkstemp(dir=self.tempdir)
            eintr_retry_call(os.close, fd)

            subprocess.call(["/usr/bin/qemu-img", "create", "-f", "qcow2", diskimage, "%sG" % size],
                            stdout=open("/dev/null", "w"))
            self._drivePaths[drive] = diskimage

    @property
    def template(self):
        with open("outside/template.py", "r") as f:
            return f.read()

    def makeSuite(self):
        """The suite is a small disk image attached to every test VM automatically
           by the test framework.  It includes all the inside/ stuff, a special
           suite.py file that will be automatically run by the live CD (and is
           what actually runs the test), and a directory structure for reporting
           results.

           It is mounted under Creator.mountpoint as needed.

           This method creates the suite image and adds it to the internal list of
           images associated with this test.

           Note that because this image is attached to the VM, anaconda will always
           see two hard drives and thus will never automatically select disks.
           Note also that this means tests must be careful to not select this
           disk.
        """
        from testconfig import config

        # First, create a disk image and put a filesystem on it.
        b = blivet.Blivet()

        # pylint: disable=undefined-variable
        disk1_path = blivet.util.create_sparse_tempfile("suite", blivet.size.Size("11 MB"))
        b.config.diskImages["suite"] = disk1_path

        b.reset()

        try:
            disk1 = b.devicetree.getDeviceByName("suite")
            b.initializeDisk(disk1)

            part = b.newPartition(size=blivet.size.Size("10 MB"), parents=[disk1])
            b.createDevice(part)

            fmt = blivet.formats.getFormat("ext4", label="ANACTEST", mountpoint=self.mountpoint)
            b.formatDevice(part, fmt)

            blivet.partitioning.doPartitioning(b)
            b.doIt()

            fmt.mount()

            # Create the directory structure needed for storing results.
            os.makedirs(self.mountpoint + "/result/anaconda")

            # Copy all the inside stuff into the mountpoint.
            shutil.copytree("inside", self.mountpoint + "/inside")

            # Create the suite file, which contains all the test cases to run and is how
            # the VM will figure out what to run.
            with open(self.mountpoint + "/suite.py", "w") as f:
                imports = map(lambda (path, cls): "    from inside.%s import %s" % (path, cls), self.tests)
                addtests = map(lambda (path, cls): "    s.addTest(%s())" % cls, self.tests)

                f.write(self.template % {"environ": "    os.environ.update(%s)" % self.environ,
                                         "imports": "\n".join(imports),
                                         "addtests": "\n".join(addtests),
                                         "anacondaArgs": config.get("anacondaArgs", "").strip('"')})
        finally:
            # pylint: disable=undefined-variable
            b.devicetree.teardownDiskImages()
            shutil.rmtree(self.mountpoint)

        # This ensures it gets passed to qemu-kvm as a disk arg.
        self._drivePaths["suite"] = disk1_path

    @contextmanager
    def suiteMounted(self):
        """This context manager allows for wrapping code that needs to access the
           suite.  It mounts the disk image beforehand and unmounts it afterwards.
        """
        if self._drivePaths.get("suite", "") == "":
            return

        b = blivet.Blivet()
        b.config.diskImages["suite"] = self._drivePaths["suite"]
        b.reset()

        disk = b.devicetree.getDeviceByName("suite")
        part = b.devicetree.getChildren(disk)[0]
        part.format.mountpoint = self.mountpoint
        part.format.mount()

        try:
            yield
        except:
            raise
        finally:
            part.format.unmount()
            # pylint: disable=undefined-variable
            b.devicetree.teardownDiskImages()

    def run(self):
        """Given disk images previously created by Creator.makeDrives and
           Creator.makeSuite, start qemu and wait for it to terminate.
        """
        from testconfig import config

        args = ["/usr/bin/qemu-kvm",
                "-vnc", "localhost:2",
                "-m", str(self.reqMemory),
                "-boot", "d",
                "-drive", "file=%s,media=cdrom" % config["liveImage"]]

        for drive in self._drivePaths.values():
            args += ["-drive", "file=%s,media=disk" % drive]

        # Save a reference to the running qemu process so we can later kill
        # it if necessary.  For now, the only reason we'd want to kill it is
        # an expired timer.
        self._proc = subprocess.Popen(args)
        self._proc.wait()
        self._proc = None

    @property
    def mountpoint(self):
        """The directory where the suite is mounted.  This is a subdirectory of
           Creator.tempdir, and it is assumed the mountpoint directory (though not
           the mount itself) exists throughout this test.
        """
        if not self._mountpoint:
            self._mountpoint = tempfile.mkdtemp(dir=self.tempdir)

        return self._mountpoint

    @property
    def tempdir(self):
        """The temporary directory used to store disk images and other data
           this test requires.  This directory will be removed by Creator.cleanup.
           It is up to the caller to call that method, though.
        """
        if not self._tempdir:
            self._tempdir = tempfile.mkdtemp(prefix="%s-" % self.name, dir="/var/tmp")

        return self._tempdir

class OutsideMixin(object):
    """A BaseOutsideTestCase subclass is the interface between the unittest framework
       and a running VM.  It interfaces with an associated Creator object to create
       devices and fire up a VM, and also handles actually reporting a result that
       unittest knows how to process.

       Each subclass will likely only want to define a single attribute:

       creatorClass -- A Creator subclass that goes with this test.
    """
    creatorClass = None

    def archive(self):
        self.creator.archive()

    def runTest(self):
        self.creator.run()

        with self.creator.suiteMounted():
            self.assertTrue(os.path.exists(self.creator.mountpoint + "/result"),
                            msg="results directory does not exist")
            self.archive()
            self.assertFalse(os.path.exists(self.creator.mountpoint + "/result/unittest-failures"),
                             msg="automated UI test %s failed" % self.creator.name)

    def setUp(self):
        # pylint: disable=not-callable
        self.creator = self.creatorClass()
        self.creator.makeDrives()
        self.creator.makeSuite()

    def tearDown(self):
        self.creator.cleanup()
