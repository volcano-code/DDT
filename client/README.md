# Linux client adaptation: public source, not an installable release

This directory now contains the self-written Python deployment/upgrade/runtime
modules, command entrypoints, packaging scripts and 20 Java compatibility and
diagnostic sources from the reviewed `0.1.0-alpha.5-dev.1` baseline.

No commercial installer, vendor JAR, derived proprietary class patch, SWT binary,
font, session, license or customer information is distributed. The public tree
also excludes precompiled JARs; source availability does not mean a fresh clone
is a prepared commercial-client installation. Private authorized inputs and
validated self-written build artifacts are required for full original-client
acceptance. Unsupported or missing inputs must fail rather than bypass checks.

For a read-only CLI check from any directory:

```bash
python3 /absolute/path/to/DDT/client/bin/ddt-linux --version
python3 /absolute/path/to/DDT/client/bin/ddt-linux --help
```

`SOURCE_IMPORT.json` identifies the original 20 reviewed Java sources. The public
CI tests standalone JFR classes, deployment rejection rules and a self-written
SWT/WebKit page. It does not execute the proprietary client or prove its login,
report export, upgrade, licensing, devices or production readiness.

No changes to original authentication or license decisions are authorized by
this repository. Original-client tests must use appropriately licensed private
inputs and a separately approved environment. Keep an existing working runtime
unchanged until an independently tested installation candidate is published.
