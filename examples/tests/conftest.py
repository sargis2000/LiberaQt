# qtdriver's pytest plugin is auto-loaded via its `pytest11` entry point when the package is
# installed. Declaring it in `pytest_plugins` as well would register the same module twice and
# pytest aborts. Uncomment only when running against a source checkout that is NOT pip-installed:
# pytest_plugins = ["qtdriver.pytest_plugin"]