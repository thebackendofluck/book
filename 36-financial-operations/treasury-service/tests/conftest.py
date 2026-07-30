# Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

import os
import sys

SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SERVICE_DIR in sys.path:
    sys.path.remove(SERVICE_DIR)
sys.path.insert(0, SERVICE_DIR)

for module_name in ("models", "treasury"):
    sys.modules.pop(module_name, None)
