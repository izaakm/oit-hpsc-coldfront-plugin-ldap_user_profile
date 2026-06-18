"""
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
            # Each entry's .entry_attributes_as_dict gives us
            # {'uid': ['jmill165'], 'uidNumber': [9540], ...}
            entries = [
                entry.entry_attributes_as_dict
                for entry in conn.entries
            ]

        return entries

    # ──────────────────────────────────────────────────────────
    # Process a single LDAP entry
    # ──────────────────────────────────────────────────────────

    def _process_entry(self, entry, options):
        """Create or update Django User + LDAPUserProfile from one LDAP entry.

        Returns one of: 'created', 'updated', 'skipped'.
        """
        # --- Extract attributes ---
        username = _first(entry.get("uid"))
        if not username:
            if self.verbosity >= 1:
                self.stdout.write(self.style.WARNING("  SKIP: entry with no uid"))
            return "skipped"

        uid_number = _first_int(entry.get("uidNumber"))
        if not uid_number:
            if self.verbosity >= 1:
                self.stdout.write(self.style.WARNING(f"  SKIP [{username}]: no uidNumber"))
            return "skipped"

        # Name fields: LDAP is inconsistent about givenName vs. cn.
        # 'cn' is often the full name, 'givenName' is the first name.
        # Fall back gracefully.
        first_name = _first(entry.get("givenName")) or _first(entry.get("cn", ""))
        last_name = _first(entry.get("sn", ""))
        email = _first(entry.get("mail", ""))

        gid_number = _first_int(entry.get("gidNumber"), default=uid_number)
        home_directory = _first(entry.get("homeDirectory", ""))
        login_shell = _first(entry.get("loginShell"), default="/bin/bash")
        gecos = _first(entry.get("gecos", ""))

        shadow_last_change = _first_int(entry.get("shadowLastChange"), default=0)
        shadow_max = _first_int(entry.get("shadowMax"), default=99999)
        shadow_warning = _first_int(entry.get("shadowWarning"), default=7)

        # host is multi-valued: ['isaac', 'all', 'login', ...]
        host_names = entry.get("host", [])
        if not isinstance(host_names, list):
            host_names = [host_names] if host_names else []

        # userPassword comes back as bytes or a base64 string.
        # Store it as-is for LDAP export; we don't need to parse it.
        raw_password = _first(entry.get("userPassword", ""))
        if isinstance(raw_password, bytes):
            ldap_password = raw_password.decode("utf-8", errors="replace")
        else:
            ldap_password = str(raw_password) if raw_password else ""

        # --- Log what we found ---
        if self.verbosity >= 2:
            self.stdout.write(f"  Processing: {username} (uid={uid_number}, gid={gid_number})")

        if self.dry_run:
            action = "created" if not User.objects.filter(username=username).exists() else "updated"
            label = self.style.SUCCESS(f"  [DRY RUN] Would {action}: {username}")
            self.stdout.write(label)
            return action

        # --- Write to database (all-or-nothing per user) ---
        with transaction.atomic():
            # 1) Django User
            user, user_created = User.objects.update_or_create(
                username=username,
                defaults={
                    "first_name": first_name[:30],   # Django User.first_name max_length=30 (150 in 4.x)
                    "last_name": last_name[:150],
                    "email": email[:254],
                },
            )

            if user_created and options.get("set_unusable_password", True):
                user.set_unusable_password()
                user.save(update_fields=["password"])

            # 2) ColdFront UserProfile
            #    The post_save signal in coldfront.core.user.signals should
            #    auto-create this, but be defensive in case it didn't fire
            #    (e.g., bulk import before signals were connected).
            from coldfront.core.user.models import UserProfile
            UserProfile.objects.get_or_create(user=user)

            # 3) LDAPUserProfile
            ldap_profile, profile_created = LDAPUserProfile.objects.update_or_create(
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

            # 4) Host entries (M2M)
            if host_names:
                host_objects = []
                for hname in host_names:
                    hname = str(hname).strip()
                    if hname:
                        host_obj, _ = LDAPHost.objects.get_or_create(name=hname)
                        host_objects.append(host_obj)
                # .set() replaces the entire M2M relationship — idempotent.
                ldap_profile.hosts.set(host_objects)

        # --- Report ---
        action = "created" if user_created else "updated"
        if self.verbosity >= 1:
            host_str = ", ".join(str(h) for h in host_names) if host_names else "(none)"
            symbol = "+" if user_created else "~"
            self.stdout.write(
                f"  [{symbol}] {username:20s}  uid={uid_number:<6d}  "
                f"gid={gid_number:<6d}  hosts=[{host_str}]"
            )

        return action
