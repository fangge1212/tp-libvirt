import logging
import re

from six import itervalues, string_types
from avocado.utils import process

from virttest import utils_selinux
from virttest import virsh
from virttest import migration
from virttest.utils_conn import TLSConnection, TCPConnection, SSHConnection
from virttest.utils_test import libvirt, VMStress
from virttest.libvirt_xml import vm_xml


class MigrationTemplate(object):
    """
    Migration template class for cross feature testing
    """

    def __init__(self, test, params, env):

        self.params = params
        self.test = test

        # Param: libvirtd remote access port and protocol
        self.remote_protocol = self.params.get("remote_protocol", "tcp")
        if self.remote_protocol == 'tls':
            self.remote_port = '16514'
        elif self.remote_protocol == 'tcp':
            self.remote_port = '16509'

        # Param: virsh migrate options
        self.virsh_migrate_options = self.params.get("virsh_migrate_options")
        self.postcopy_options = self.params.get("postcopy_options")
        self.native_tls_options = self.params.get("native_tls_options")
        if self.postcopy_options:
            self.virsh_migrate_options += " %s" % self.postcopy_options
        if self.native_tls_options:
            self.virsh_migrate_options += " %s" % self.native_tls_options
        self.virsh_migrate_extra = self.params.get("virsh_migrate_extra", "")
        self.migrate_options_all = self.virsh_migrate_options + self.virsh_migrate_extra

        # Param: migration thread timeout
        self.thread_timeout = int(self.params.get("thread_timeout", "900"))

        # Param: migrate srcuri and desturi
        self.hypervisor_driver = self.params.get("hypervisor_driver", "qemu")
        self.hypervisor_mode = self.params.get("hypervisor_mode", 'system')
        self.server_cn = self.params.get("server_cn")
        self.client_cn = self.params.get("client_cn")
        self.migrate_source_host = self.client_cn if self.client_cn else self.params.get("migrate_source_host")
        self.migrate_dest_host = self.server_cn if self.server_cn else self.params.get("migrate_dest_host")
        if "virsh_migrate_desturi" not in list(self.params.keys()):
            self.params["virsh_migrate_desturi"] = "%s+%s://%s/%s" % (self.hypervisor_driver,
                                                                      self.remote_protocol,
                                                                      self.migrate_dest_host,
                                                                      self.hypervisor_mode)
        if "virsh_migrate_srcuri" not in list(self.params.keys()):
            self.params["virsh_migrate_srcuri"] = "qemu:///system"
        self.dest_uri = self.params.get("virsh_migrate_desturi")
        self.src_uri = self.params.get("virsh_migrate_srcuri")

        # Param: whether to migrate vm back to src
        self.migrate_vm_back = self.params.get("migrate_vm_back", "yes")

        # Param: vms for migration test, only use migrate_main_vm for now
        self.migrate_main_vm = self.params.get("migrate_main_vm")
        self.vms = []

        # MigrationTest instance
        logging.debug("Get a migrationtest object")
        self.obj_migration = migration.MigrationTest()

        # VM instance for migration
        logging.debug("Get a vm object for migration")
        self.vm = env.get_vm(self.migrate_main_vm)
        self.vms.append(self.vm)

        # Variable: vm xml backup for vm recovery
        self.vm_xml_backup = None

        # Variable: test result check
        self.result_check_pass = True

        # Variable: objects(ssh, tls and tcp, etc) to be cleaned up in finally
        self.objs_list = []

    def runtest(self):
        # Check whether there are unset parameters
        for v in list(itervalues(self.params)):
            if isinstance(v, string_types) and v.count("EXAMPLE"):
                self.test.cancel("Please set real value for %s" % v)

        self._pre_start_vm()
        self._start_vm()
        self._post_start_vm()
        self._pre_migrate()
        self._migrate()
        self._post_migrate()
        if self.migrate_vm_back == "yes":
            self._migrate_back()
            self._post_migrate_back()

    def _setup_host(self):
        # Setup libvirtd remote connection env
        logging.debug("Setup libvirtd remote access env")
        conn_dict = {'tls': TLSConnection,
                     'tcp': TCPConnection,
                     'ssh': SSHConnection}
        remote_conn_obj = conn_dict[self.remote_protocol](self.params)
        remote_conn_obj.conn_setup()
        remote_conn_obj.auto_recover = True
        self.objs_list.append(remote_conn_obj)
        if self.migrate_vm_back == 'yes':
            logging.debug("Setup libvirtd remote access env for reverse migration")
            back_params = dict(self.params)
            back_params['server_ip'] = self.params.get('client_ip')
            back_params['client_ip'] = self.params.get('server_ip')
            back_params['server_cn'] = self.params.get('client_cn')
            back_params['client_cn'] = self.params.get('server_cn')
            back_params['server_pwd'] = self.params.get('client_pwd')
            back_params['client_pwd'] = self.params.get('server_pwd')
            if self.remote_protocol == 'tls':
                back_params['ca_cakey_path'] = remote_conn_obj.pki_CA_dir
                back_params['scp_new_cacert'] = 'no'
            back_remote_conn_obj = conn_dict[self.remote_protocol](back_params)
            back_remote_conn_obj.conn_setup()
            back_remote_conn_obj.auto_recover = True
            self.objs_list.append(back_remote_conn_obj)

        # Enable libvirtd remote port in firewalld
        logging.debug("Enable libvirtd remote port in firewalld on dst host")
        self.obj_migration.migrate_pre_setup(self.dest_uri, self.params, ports=self.remote_port)
        if self.migrate_vm_back == 'yes':
            logging.debug("Enable libvirtd remote port in firewalld on src host")
            self.obj_migration.migrate_pre_setup(self.src_uri, self.params, ports=self.remote_port)

        # Setup qemu tls env for encrypted migration
        if self.migrate_options_all.count('--tls'):
            logging.debug("Setup qemu tls env")
            qemu_tls_params = dict(self.params)
            qemu_tls_params['custom_pki_path'] = '/etc/pki/qemu'
            qemu_tls_params['qemu_tls'] = 'yes'
            qemu_tls_obj = TLSConnection(qemu_tls_params)
            qemu_tls_obj.conn_setup()
            qemu_tls_obj.auto_recover = True
            self.objs_list.append(qemu_tls_obj)
            if self.migrate_vm_back == 'yes':
                logging.debug("Setup qemu tls env for reverse migration")
                qemu_tls_params['server_ip'] = self.params.get('client_ip')
                qemu_tls_params['client_ip'] = self.params.get('server_ip')
                qemu_tls_params['server_cn'] = self.params.get('client_cn')
                qemu_tls_params['client_cn'] = self.params.get('server_cn')
                qemu_tls_params['server_pwd'] = self.params.get('client_pwd')
                qemu_tls_params['client_pwd'] = self.params.get('server_pwd')
                qemu_tls_params['ca_cakey_path'] = qemu_tls_params.get('custom_pki_path')
                qemu_tls_params['scp_new_cacert'] = 'no'
                back_qemu_tls_obj = TLSConnection(qemu_tls_params)
                back_qemu_tls_obj.conn_setup()
                back_qemu_tls_obj.auto_recover = True
                self.objs_list.append(back_qemu_tls_obj)

        # Set selinux state before migration
        logging.debug("Set selinux to enforcing before migration")
        utils_selinux.set_status(self.params.get("selinux_state", "enforcing"))
        # TODO: Set selinux on migrate_dest_host

        # TODO: Setup host according to your test

    def _setup_vm(self):
        # Back up vm xml for recovery
        logging.debug("Backup vm xml before migration")
        self.vm_xml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(self.vm.name)
        if not self.vm_xml_backup:
            self.test.error("Backing up xmlfile failed.")

        # Clean up vm on dest host
        logging.debug("Clean up vm on dest host before migration")
        self.obj_migration.cleanup_dest_vm(self.vm, self.src_uri, self.dest_uri)

        # Destroy vm on src host if it's alive
        logging.debug("Destroy vm on src host")
        if self.vm.is_alive():
            self.vm.destroy()

        # Set shared disk in vm xml:
        # 1) change the source of the first disk of vm to shared disk
        # 2) change the disk cache mode to none
        logging.debug("Prepare shared disk in vm xml for live migration")
        storage_type = self.params.get("storage_type")
        if storage_type == 'nfs':
            logging.debug("Prepare nfs shared disk in vm xml")
            nfs_mount_dir = self.params.get("nfs_mount_dir")
            libvirt.update_vm_disk_source(self.vm.name, nfs_mount_dir)
            libvirt.update_vm_disk_driver_cache(self.vm.name, driver_cache="none")
        else:
            # TODO:other storage types
            self.test.cancel("Other storage type is not supported for now")
            pass

        # TODO: vm related operations before vm starts according to your test

    def _pre_start_vm(self):
        self._setup_host()
        self._setup_vm()

    def _start_vm(self):
        # Start vm.
        logging.debug("Start vm %s.", self.vm.name)
        self.vm.start()

    def _post_start_vm(self):
        # Operations after vm starts
        # e.g. hotplug device, check vm xml, check device functionality
        pass

    def _pre_migrate(self):
        # Check vm network connectivity before migration
        logging.debug("Check vm network before migration")
        self.obj_migration.ping_vm(self.vm, self.params)

        # Check vm uptime before migration
        self.uptime = {}
        self.uptime[self.vm.name] = self.vm.uptime()

        # Install and run stress in vm
        if self.migrate_options_all.count("--postcopy"):
            stress_obj = VMStress(self.vm, "stress", self.params)
            stress_obj.load_stress_tool()

    def _migrate(self):
        # Start to do migration.
        # NOTE: vm.connect_uri will be set to dest_uri once migration is complete successfully
        logging.debug("Start to do migration")
        if self.migrate_options_all.count("--postcopy"):
            # Monitor the qemu monitor event of "Suspended Post-copy" for postcopy migration
            logging.debug("Monitor the event for postcopy migration")
            virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC, auto_close=True)
            self.objs_list.append(virsh_session)
            cmd = "event %s --loop --all --timestamp" % vm.name
            virsh_session.sendline(cmd)

            # Do live migration and switch to postcopy by "virsh migrate-postcopy"
            logging.debug("start to do postcopy migration")
            self.obj_migration.do_migration(self.vms, self.src_uri, self.dest_uri, "orderly",
                                            options=self.virsh_migrate_options,
                                            thread_timeout=self.thread_timeout,
                                            ignore_status=False,
                                            func=virsh.migrate_postcopy,
                                            extra_opts=self.virsh_migrate_extra,
                                            shell=True)

            # Check "suspended post-copy" event after postcopy migration
            logging.debug("Check event after postcopy migration")
            virsh_session.send_ctrl("^c")
            events_output = virsh_session.get_stripped_output()
            logging.debug("Events_output are %s", events_output)
            pattern = "Suspended Post-copy"
            if not re.search(pattern, events_output):
                self.test.fail("Migration didn't switch to postcopy mode")

        else:
            logging.debug("start to do precopy migration")
            self.obj_migration.do_migration(self.vms, self.src_uri, self.dest_uri, "orderly",
                                            options=self.virsh_migrate_options,
                                            thread_timeout=self.thread_timeout,
                                            ignore_status=False,
                                            extra_opts=self.virsh_migrate_extra,
                                            shell=True)

    def _post_migrate(self):
        # Check vm uptime/status/network after migration
        self.params["migrate_options"] = self.migrate_options_all
        logging.debug("Do post migration check")
        self.obj_migration.post_migration_check(self.vms, self.params, self.uptime, uri=self.vm.connect_uri)
        # Restore vm.connect_uri as it is set to src_uri in ping_vm()
        self.vm.connect_uri = self.dest_uri

        # TODO: checkpoints after migration according to your test, and if check fails:
        #    self.result_check_pass = False
        #    logging.error("xxxx")

        # e.g.check dst vm active xml after migration
        # logging.debug("check vm xml on target after migration")
        # remote_virsh = virsh.Virsh(uri=self.vm.connect_uri)
        # vmxml_active_tmp = vm_xml.VMXML.new_from_dumpxml(self.vm.name, "--security-info",
        #                                                  remote_virsh)
        # check dst vm active xml according to your test, and if check fails:
        #    self.result_check_pass = False
        #    logging.error("xxxx")

    def _migrate_back(self):
        # Migrate vm back to src host
        logging.debug("Start to migrate vm back to src host")
        logging.debug("Enable migration port on src host in firewalld")
        self.obj_migration.migrate_pre_setup(self.src_uri, self.params)
        src_uri = "%s+%s://%s/%s" % (self.hypervisor_driver,
                                     self.remote_protocol,
                                     self.migrate_source_host,
                                     self.hypervisor_mode)
        self.obj_migration.do_migration(self.vms, self.dest_uri, src_uri, "orderly",
                                        options=self.virsh_migrate_options,
                                        thread_timeout=self.thread_timeout,
                                        ignore_status=False,
                                        extra_opts=self.virsh_migrate_extra,
                                        virsh_uri=self.dest_uri,
                                        shell=True)

    def _post_migrate_back(self):
        # Check vm uptime/status/network after migration
        logging.debug("Do post migration check after migrate back")
        self.obj_migration.post_migration_check(self.vms, self.params, self.uptime, uri=self.vm.connect_uri)

        # TODO: checkpoints after migration back according to your test, and if check fails:
        #    self.result_check_pass = False
        #    logging.error("xxxx")

    def cleanup(self):
        logging.debug("Start to clean up env")
        # Clean up vm on dest host
        self.obj_migration.cleanup_dest_vm(self.vm, self.src_uri, self.dest_uri)

        # Shutdown vm on src host
        self.vm.destroy()

        # Recover source vm defination (just in case).
        logging.info("Recover vm defination on source")
        if self.vm_xml_backup:
            self.vm_xml_backup.define()

        # Clean up ssh, tcp, tls test env
        if self.objs_list and len(self.objs_list) > 0:
            logging.debug("Clean up test env: ssh, tcp, tls, etc")
            self.objs_list.reverse()
            for obj in self.objs_list:
                obj.__del__()

        # Disable libvirtd remote connection port
        if self.remote_port is not None:
            self.obj_migration.migrate_pre_setup(self.dest_uri, self.params, cleanup=True, ports=self.remote_port)
            self.obj_migration.migrate_pre_setup(self.src_uri, self.params, cleanup=True, ports=self.remote_port)

        # Check test result.
        if not self.result_check_pass:
            self.test.fail("migration succeed, but some checkpoints didn't pass."
                           "please check the error log for details")
