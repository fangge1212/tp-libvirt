import aexpect

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    This test virsh domfsfreeze and domfsthaw commands and their options.

    1) Start a guest with/without guest agent configured;
    2) Freeze the guest file systems with domfsfreeze;
    3) Create a file on guest to see command hang;
    4) Thaw the guest file systems with domfsthaw;
    5) Check the file is already created;
    6) Retouch the file the ensure guest file system are not frozen;
    7) Cleanup test environment.
    """
    def check_freeze(session, file_name):
        """
        Check whether file system has been frozen by touch a test file
        and see if command will hang.

        :param session: Guest session to be tested.
        :param file_name: The name of file to be created on guest.
        """
        try:
            output = session.cmd_output('touch freeze_test',
                                        timeout=10)
            test.fail("Failed to freeze file system. "
                      "Create file succeeded:\n%s" % output)
        except aexpect.ShellTimeoutError:
            pass

    def check_thaw(session, file_name):
        """
        Check whether file system has been thawed by check a test file
        prohibited from creation when frozen created and successfully touch
        the file again.

        :param session: Guest session to be tested.
        :param file_name: The name of file to be checked on guest.
        """
        status, output = session.cmd_status_output('ls %s' % file_name)
        if status:
            test.fail("Failed to thaw file system. "
                      "Find created file failed:\n%s" % output)

        try:
            output = session.cmd_output('touch %s' % file_name, timeout=10)
        except aexpect.ShellTimeoutError:
            test.fail("Failed to thaw file system. "
                      "Touch file timeout:\n%s" % output)

    def generate_random_string(length=10):
        """
        Generate a random string with specified length.
        """
        import random
        import string

        return ''.join(random.choice(string.ascii_letters + string.digits)
                       for _ in range(length))

    def run_agent_command_when_frozen(vm_name, command_name='domtime'):
        """
        Run qemu guest agent command when guest filesystem is fronzen.
        It should fail with reasonable error.

        Args:
            vm_name (string): Vm name
            command_name (string): Qemu agent command name, e.g. domtime
        Returns:
            None
        """
        fail_patts = [
            r"error: guest agent command failed: unable to execute QEMU agent command '\S+': Command guest-get-time has been disabled: the command is not allowed",
            r"error: internal error: unable to execute QEMU agent command '\S+': Command guest-get-time has been disabled: the command is not allowed"
        ]

        virsh_command = eval('virsh.%s' % command_name)
        res = virsh_command(vm_name)
        libvirt.check_result(res, fail_patts)

    def run_agent_command_when_thawed(vm_name, command_name='domtime'):
        """
        Run qemu guest agent command when guest filesystem is thawed.
        It should succeed.

        Args:
            vm_name (string): Vm name
            command_name (string): Qemu agent command name, e.g. domtime
        Returns:
            None
        """
        virsh_command = eval('virsh.%s' % command_name)
        res = virsh_command(vm_name)
        libvirt.check_exit_status(res, expect_error=False)

    def cleanup(session):
        """
        Clean up the test file used for freeze/thaw test.

        :param session: Guest session to be cleaned up.
        """
        status, output = session.cmd_status_output('rm -f freeze_test')
        if status:
            test.error("Failed to cleanup test file"
                       "Find created file failed:\n%s" % output)

    if not virsh.has_help_command('domfsfreeze'):
        test.cancel("This version of libvirt does not support "
                    "the domfsfreeze/domfsthaw test")

    channel = ("yes" == params.get("prepare_channel", "yes"))
    agent = ("yes" == params.get("start_agent", "yes"))
    mountpoint = params.get("mountpoint", None)
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    file_name = generate_random_string() + "_freeze_test"

    xml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    try:
        # Add or remove qemu-agent from guest before test
        vm.prepare_guest_agent(channel=channel, start=agent)
        session = vm.wait_for_login()
        try:
            # Expected fail message patterns
            fail_patts = []
            if not channel:
                fail_patts.append(r"QEMU guest agent is not configured")
            if not agent:
                # For older version
                fail_patts.append(r"Guest agent not available for now")
                # For newer version
                fail_patts.append(r"Guest agent is not responding")
            # Message patterns test should skip when met
            skip_patts = [
                r'The command \S+ has not been found',
                r'specifying mountpoints is not supported',
            ]

            res = virsh.domfsfreeze(vm_name, mountpoint=mountpoint)
            libvirt.check_result(res, fail_patts, skip_patts)
            if not res.exit_status:
                check_freeze(session, file_name)

            run_agent_command_when_frozen(vm_name)

            res = virsh.domfsthaw(vm_name)
            libvirt.check_result(res, fail_patts, skip_patts)
            if not res.exit_status:
                check_thaw(session, file_name)

            run_agent_command_when_thawed(vm_name)

            cleanup(session)
        finally:
            session.close()
    finally:
        xml_backup.sync()
