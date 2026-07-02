"""
coldfront/plugins/ldap_user_profile/management/commands/import_users_from_ldap.py

Usage:
    python manage.py import_users_from_ldap
    python manage.py import_users_from_ldap --dry-run
    python manage.py import_users_from_ldap --verbosity 2
    python manage.py import_users_from_ldap --username jmill165
    python manage.py import_users_from_ldap --search-filter '(uid=jmill165)'
"""

import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from coldfront.plugins.ldap_user_profile.models import LDAPHost, LDAPUserProfile

# Import your existing client
from coldfront.plugins.ldap_user_profile.ldap_client import ISAACLDAP

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _first(values, default=""):
    """Extract the first element from an LDAP attribute value list.

    LDAP attributes come back as lists (e.g. {'cn': ['Izaak']}),
    even for single-valued attributes. This safely grabs [0].
    """
    if isinstance(values, list) and values:
        return values[0]
    if values and not isinstance(values, list):
        return values
    return default


def _first_int(values, default=0):
    """Like _first, but coerces to int."""
    val = _first(values, default=default)
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


class Command(BaseCommand):
    help = (
        "Import users from LDAP into the ColdFront database. "
        "Creates or updates Django User, ColdFront UserProfile, "
        "and LDAPUserProfile records for each LDAP entry."
    )

    # ──────────────────────────────────────────────────────────
    # CLI arguments
    # ──────────────────────────────────────────────────────────

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Show what would be imported without writing to the database.",
        )
        parser.add_argument(
            "--username",
            type=str,
            default=None,
            help="Import only this single uid (username). Useful for testing.",
        )
        parser.add_argument(
            "--search-filter",
            type=str,
            default=None,
            help=(
                "Override the default LDAP search filter. "
                "Default: '(objectClass=posixAccount)'"
            ),
        )
        parser.add_argument(
            "--search-base",
            type=str,
            default=None,
            help=(
                "Override the LDAP search base DN. "
                "Default: from settings.LDAP_SEARCH_BASE or the client's search_base."
            ),
        )
        parser.add_argument(
            "--set-unusable-password",
            action="store_true",
            default=True,
            help=(
                "Set an unusable Django password for imported users (default). "
                "This means they cannot log in via Django's auth backend; "
                "use an LDAP auth backend instead."
            ),
        )

    # ──────────────────────────────────────────────────────────
    # Main entry point
    # ──────────────────────────────────────────────────────────

    def handle(self, *args, **options):
        self.dry_run = options["dry_run"]
        self.verbosity = options["verbosity"]

        if self.dry_run:
            self.stdout.write(self.style.WARNING("*** DRY RUN — no changes will be written ***\n"))

        # ----- Build LDAP connection from settings -----
        client = self._build_client()

        # ----- Determine search parameters -----
        search_base = (
            options["search_base"]
            or getattr(settings, "LDAP_SEARCH_BASE", None)
            or client.search_base
        )
        if not search_base:
            raise CommandError(
                "No search base configured. Set LDAP_SEARCH_BASE in settings, "
                "pass --search-base, or configure it on the ISAACLDAP client."
            )

        if options["username"]:
            search_filter = f"(&(objectClass=posixAccount)(uid={options['username']}))"
        elif options["search_filter"]:
            search_filter = options["search_filter"]
        else:
            search_filter = "(objectClass=posixAccount)"

        # ----- Fetch entries from LDAP -----
        entries = self._fetch_entries(client, search_base, search_filter)
        self.stdout.write(f"Found {len(entries)} LDAP entries.\n")

        if not entries:
            self.stdout.write(self.style.WARNING("Nothing to import."))
            return

        # ----- Process each entry -----
        stats = {"created": 0, "updated": 0, "skipped": 0, "errors": 0}

        for entry_dict in entries:
            try:
                action = self._process_entry(entry_dict, options)
                stats[action] += 1
            except Exception as e:
                uid = _first(entry_dict.get("uid", ["???"]))
                logger.exception("Error processing uid=%s", uid)
                self.stderr.write(self.style.ERROR(f"  ERROR [{uid}]: {e}"))
                stats["errors"] += 1

        # ----- Summary -----
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created: {stats['created']}  "
                f"Updated: {stats['updated']}  "
                f"Skipped: {stats['skipped']}  "
                f"Errors: {stats['errors']}"
            )
        )
        if self.dry_run:
            self.stdout.write(self.style.WARNING("(dry run — nothing was actually written)"))

    # ──────────────────────────────────────────────────────────
    # LDAP connection
    # ──────────────────────────────────────────────────────────

    def _build_client(self):
        """Construct an ISAACLDAP client from Django settings.

        Expected settings (in local_settings.py or settings.py):
            LDAP_HOST          = 'ldaps://ldap.hpsc.tennessee.edu'
            LDAP_PORT          = 636
            LDAP_USE_SSL       = True
            LDAP_BIND_DN       = 'cn=admin,dc=hpsc,dc=tennessee,dc=edu'
            LDAP_BIND_PASSWORD = 'secret'  # or use env var
            LDAP_SEARCH_BASE   = 'ou=People,dc=hpsc,dc=tennessee,dc=edu'
        """
        host = getattr(settings, "LDAP_HOST", None)
        if not host:
            raise CommandError("LDAP_HOST is not configured in Django settings.")

        return ISAACLDAP(
            host=host,
            port=getattr(settings, "LDAP_PORT", None),
            use_ssl=getattr(settings, "LDAP_USE_SSL", False),
            user=getattr(settings, "LDAP_BIND_DN", None),
            password=getattr(settings, "LDAP_BIND_PASSWORD", None),
            search_base=getattr(settings, "LDAP_SEARCH_BASE", None),
            read_only=True,  # we're only reading from LDAP
        )

    # ──────────────────────────────────────────────────────────
    # LDAP search
    # ──────────────────────────────────────────────────────────

    # The LDAP attributes we need to pull. Requesting specific attributes
    # is more efficient than pulling everything with ['*'].
    LDAP_ATTRIBUTES = [
        "uid",
        "cn",
        "sn",
        "givenName",
        "mail",
        "uidNumber",
        "gidNumber",
        "homeDirectory",
        "loginShell",
        "gecos",
        "shadowLastChange",
        "shadowMax",
        "shadowWarning",
        "host",
        "userPassword",
        "objectClass",
    ]

    def _fetch_entries(self, client, search_base, search_filter):
        """Search LDAP and return a list of attribute dicts."""
        if self.verbosity >= 2:
            # self.stdout.write(f"  Client:        {client}")  # Not helpful, just object id.
            self.stdout.write(f"  Connection:    {client.connection}")
            self.stdout.write(f"  Search base:   {search_base}")
            self.stdout.write(f"  Search filter: {search_filter}")
            self.stdout.write(f"  Attributes:    {self.LDAP_ATTRIBUTES}\n")

        with client.connection as conn:
            conn.search(
                search_base=search_base,
                search_filter=search_filter,
                size_limit=0,
                attributes=self.LDAP_ATTRIBUTES,
            )
            # conn.entries is only valid inside the context manager,
            # so materialise everything into plain dicts now.
            entries = [
                entry.entry_attributes_as_dict
                for entry in conn.entries
            ]

        if self.verbosity >= 2:
            self.stdout.write(f"  Raw entries returned: {len(entries)}")

        return entries

    # ──────────────────────────────────────────────────────────
    # Process one LDAP entry → Django/ColdFront/LDAP models
    # ──────────────────────────────────────────────────────────

    @transaction.atomic
    def _process_entry(self, entry_dict, options):
        """Create or update the three models for one LDAP user.

        Creates:
          1. Django User          (username, first_name, last_name, email)
          2. ColdFront UserProfile (is_pi — defaults to False)
          3. LDAPUserProfile      (all POSIX/LDAP fields)
          4. LDAPHost M2M links

        Returns one of: 'created', 'updated', 'skipped'.
        """
        # --- Extract fields from the LDAP attribute dict ---
        username = _first(entry_dict.get("uid"))
        if not username:
            self.stderr.write(self.style.WARNING(
                "  SKIP: entry has no uid attribute"
            ))
            return "skipped"

        # Name: prefer givenName for first name, fall back to cn
        first_name = (
            _first(entry_dict.get("givenName"))
            or _first(entry_dict.get("cn"))
        )
        last_name = _first(entry_dict.get("sn"))
        email = _first(entry_dict.get("mail"))

        uid_number = _first_int(entry_dict.get("uidNumber"))
        gid_number = _first_int(entry_dict.get("gidNumber"))
        home_directory = _first(entry_dict.get("homeDirectory"))
        login_shell = _first(entry_dict.get("loginShell"), "/bin/bash")
        gecos = _first(entry_dict.get("gecos"))

        shadow_last_change = _first_int(
            entry_dict.get("shadowLastChange"), 0
        )
        shadow_max = _first_int(entry_dict.get("shadowMax"), 99999)
        shadow_warning = _first_int(entry_dict.get("shadowWarning"), 7)

        # userPassword comes as bytes from ldap3
        raw_pw = _first(entry_dict.get("userPassword"), "")
        if isinstance(raw_pw, bytes):
            # Store the base64 representation of the hashed password
            import base64
            ldap_password = base64.b64encode(raw_pw).decode("ascii")
        else:
            ldap_password = str(raw_pw)

        # host is multi-valued
        host_names = entry_dict.get("host", [])
        if isinstance(host_names, str):
            host_names = [host_names]

        if self.verbosity >= 2:
            self.stdout.write(f"  Processing: {username} "
                              f"(uid={uid_number}, gid={gid_number})")

        if self.dry_run:
            self.stdout.write(f"  [DRY RUN] Would import: {username} "
                              f"({first_name} {last_name}, {email})")
            return "created"  # count it for reporting purposes

        # ── 1. Django User ──────────────────────────────────
        user, user_created = User.objects.update_or_create(
            username=username,
            defaults={
                "first_name": first_name[:30],   # Django's max_length=30 (pre-4.x) or 150
                "last_name": last_name[:150],
                "email": email[:254] if email else "",
            },
        )

        if user_created and options.get("set_unusable_password", True):
            user.set_unusable_password()
            user.save(update_fields=["password"])

        # ── 2. ColdFront UserProfile ────────────────────────
        # Explicitly create it — do NOT rely on the post_save signal,
        # which may not fire depending on app loading order or if
        # update_or_create hit the "update" path.
        UserProfile.objects.get_or_create(
            user=user,
            defaults={"is_pi": False},
        )

        # ── 3. LDAPUserProfile ──────────────────────────────
        ldap_profile, ldap_created = LDAPUserProfile.objects.update_or_create(
            user=user,
            defaults={
                "uid_number": uid_number,
                "gid_number": gid_number,
                "home_directory": home_directory,
                "login_shell": login_shell,
                "gecos": gecos,
                "shadow_last_change": shadow_last_change,
                "shadow_max": shadow_max,
                "shadow_warning": shadow_warning,
                "ldap_password": ldap_password,
            },
        )

        # ── 4. Host M2M ────────────────────────────────────
        if host_names:
            host_objects = []
            for hname in host_names:
                hname = str(hname).strip()
                if hname:
                    host_obj, _ = LDAPHost.objects.get_or_create(name=hname)
                    host_objects.append(host_obj)
            # .set() replaces existing M2M links (idempotent)
            ldap_profile.hosts.set(host_objects)

        action = "created" if user_created else "updated"

        if self.verbosity >= 1:
            symbol = "+" if user_created else "~"
            self.stdout.write(
                f"  [{symbol}] {username:<20s}  "
                f"uid={uid_number}  gid={gid_number}  "
                f"hosts={[h.name for h in ldap_profile.hosts.all()]}"
            )

        return action
