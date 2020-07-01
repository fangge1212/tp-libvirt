from migrate_template_2 import MigrationTemplate
from virttest import utils_misc
from virttest.libvirt_xml.devices.tpm import Tpm
from virttest.libvirt_xml.vm_xml import VMXML

class MigrationExample(MigrationTemplate):
    """This is an example that uses classs MigrationTemplate"""
    def __init__(self, test, params, env):
        super(MigrationExample, self).__init__(test, params, env)

    def _install_swtpm_on_host(self):
        if not utils_misc.compare_qemu_version(4, 0, 0, is_rhev=False):
            self.test.cancel("vtpm(emulator backend) is not supported "
                        "on current qemu version.")
        # Install swtpm pkgs on host for vtpm emulation
        if not utils_package.package_install("swtpm*"):
            self.test.error("Failed to install swtpm swtpm-tools on host")

    def _setup_host(self):
        super(MigrationExample, self)._setup_host()
        #self._install_swtpm_on_host()

    def _set_vm_tpm_device(self):
        tpm_dev = Tpm()

        tpm_dev.tpm_model = self.params.get('tmp_model')

        backend = tpm_dev.Backend()
        backend.backend_type = self.params.get('backend_type')
        backend.backend_version = self.params.get('backend_version')
        tpm_dev.backend = backend

        logging.debug("tpm dev xml to add is:\n %s", tpm_dev)
        vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
        vm_xml.add_device(tpm_dev, True)
        vm_xml.sync()

    def _setup_vm(self):
        super(MigrationExample, self)._setup_vm()
        #self._set_vm_tpm_device()

    def _check_vm_xml(self):
        """Check vm dumpxml after vm starts"""
        pass

    def _check_qemu_cmd_line(self):
        """Check qemu cmd line after vm starts"""
        pass

    def _test_guest_tpm(self):
        """Test tpm function in guest"""
        pass

    def _post_start_vm(self):
        self._check_vm_xml()
        self._check_qemu_cmd_line()
        self._test_guest_tpm()

    def _post_migrate(self):
        super(MigrationExample, self)._post_migrate()
        self._check_vm_xml()
        self._check_qemu_cmd_line()
        self._test_guest_tpm()

def run(test, params, env):
    migration_example = MigrationExample(test, params, env)
    try:
        migration_example.runtest()
    finally:
        migration_example.cleanup()

