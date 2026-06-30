# LDAP User Profile

Don't forget!!!

    export PLUGIN_LDAP_USER_PROFILE=True


# Bulk import users from existing LDAP

    cd <coldfront root>
    python manage.py makemigrations
    python manage.py migrate
    python manage.py import_users_from_ldap -v3

## Issue [IN PROGRESS]

    System check identified some issues:

    WARNINGS:
    ?: (django_vite.W001) Cannot read Vite manifest file for app default at static/manifest.json : [Errno 2] No such file or directory: 'static/manifest.json'
        HINT: Make sure you have generated a manifest file, and that DJANGO_VITE["default"]["manifest_path"] points to the correct location.
    CommandError: LDAP_HOST is not configured in Django settings.

Setting LDAP_HOST in `coldfront.env` didn't work.

Setting LDAP_HOST in `local_settings.py` didn't work at first, but I hardcoded
the path to the file into `coldfronthe/coldfront/config/settings.py`:

```python
# Local settings overrides
local_configs = [
    ...,
    '/Users/jmill165/projects/coldfront-dev-pip-local/config/local_settings.py'
]
```

which has these settings:

```python
# ldap_user_profile
LDAP_HOST = "localhost"
LDAP_PORT = 389
LDAP_USE_SSL = False
LDAP_BIND_DN = "cn=admin,dc=hpsc,dc=tennessee,dc=edu"
LDAP_BIND_PASSWORD = "admin"
LDAP_SEARCH_BASE = "dc=hpsc,dc=tennessee,dc=edu"
```

***[TODO] Figoure out the correct location for `local_settings.py`.***

## Issue [SOLVED]

    ERROR [jdoe]: no such table: auth_user

Solution: Run `migrate` commands.



