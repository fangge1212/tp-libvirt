import ast
import aexpect
import logging

from six import iteritems

from avocado.utils import process

from virttest.libvirt_xml import vm_xml, xcepts
from virttest.libvirt_xml.devices import rng
from virttest.utils_test import libvirt
from virttest import utils_misc
from virttest import virsh, libvirt_version, migration_template
from virttest.migration_template import MigrationTemplate, Error


class migration_with_memory(MigrationTemplate):
    """
    Do migration with variaous memory settings
    """

    def __init__(self, test, env, params, *args, **dargs):
        for k, v in iteritems(dict(*args, **dargs)):
            params[k] = v
        super(migration_with_rng, self).__init__(test, env, params, *args,
                                                 **dargs)

        self.hugepage_size = params.get("hugepage_size", "2048K")

