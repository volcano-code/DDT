# Linux compatibility source import — partial repository migration

`java/` contains the 20 self-written compatibility and diagnostic Java sources
from client `0.1.0-alpha.5-dev.1`. They are not a copy of the vendor client.
No vendor JAR, Windows installer, derived proprietary class patch, SWT binary,
font, session, account or license is distributed here.

This directory is a reviewed source import, **not yet a standalone build or an
installable release**. The Python deployment/upgrade modules and the accounting
workbench are still pending public-repository migration. Several classes refer
to vendor API types and can only be compiled/tested with appropriately authorized
private inputs. They must not be tested by publishing those inputs here.

The root public CI compiles only the independent JFR classes and its self-written
SWT/WebKit platform probe. Its success must not be described as original-client
login, original-export, upgrade, accounting, or production acceptance.

Source archive identity: `ddt-integrated-cloud-r2-20261021.zip`, SHA-256
`c37bb741f7af3a4e5396874003427be3935d9cba5dd9e147fd92a24d792fb1b7`.
Per-file identities are recorded in `SOURCE_IMPORT.json`.
