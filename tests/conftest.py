import os

# Tests must not depend on the developer's local .env. load_dotenv() never
# overrides variables that are already set, so blank the editor password before
# the app is imported; tests that cover authentication set their own.
os.environ["ADMIN_PASSWORD"] = ""
