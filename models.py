"""
coldfront_plugin_ldap_export/models.py

A separate Django app that adds LDAP/POSIX attributes to ColdFront users
without modifying ColdFront's core UserProfile model.

Install:
  1. Add 'coldfront_plugin_ldap_export' to INSTALLED_APPS in local_settings.py
  2. Run: python manage.py makemigrations coldfront_plugin_ldap_export
  3. Run: python manage.py migrate
"""

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


class LDAPHost(models.Model):
    """Represents an LDAP 'host' value (e.g., 'isaac', 'all', 'login').

    Stored as a separate model because a user can have multiple host entries.
    """

    name = models.CharField(max_length=255, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class LDAPUserProfile(models.Model):
    """Stores POSIX/LDAP attributes for a ColdFront user.

    This sits alongside ColdFront's UserProfile (which tracks is_pi)
    without interfering with it. Both use OneToOneField to User.

    Usage:
        user.ldapuserprofile.uid_number
        user.ldapuserprofile.hosts.all()
    """

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="ldapuserprofile",
    )

    # --- POSIX account attributes ---
    uid_number = models.PositiveIntegerField(
        unique=True,
        help_text="POSIX uidNumber. Must be unique across all users.",
    )
    gid_number = models.PositiveIntegerField(
        help_text="Primary POSIX gidNumber.",
    )
    home_directory = models.CharField(
        max_length=512,
        blank=True,
        help_text="POSIX home directory path, e.g. /nfs/home/jmill165",
    )
    login_shell = models.CharField(
        max_length=255,
        default="/bin/bash",
        help_text="POSIX login shell.",
    )
    gecos = models.CharField(
        max_length=512,
        blank=True,
        help_text="GECOS field. Auto-generated from user's name if left blank.",
    )

    # --- Shadow password attributes ---
    shadow_last_change = models.IntegerField(
        default=0,
        help_text="Days since epoch of last password change (shadowLastChange).",
    )
    shadow_max = models.IntegerField(
        default=99999,
        help_text="Max days before password must be changed (shadowMax).",
    )
    shadow_warning = models.IntegerField(
        default=7,
        help_text="Days before expiry to warn user (shadowWarning).",
    )

    # --- Host access ---
    hosts = models.ManyToManyField(
        LDAPHost,
        blank=True,
        help_text="LDAP host entries this user is authorized for.",
    )

    # --- Password (hashed, for LDAP only — NOT Django's auth password) ---
    ldap_password = models.CharField(
        max_length=1024,
        blank=True,
        help_text=(
            "LDAP userPassword (pre-hashed, e.g. {SSHA384}...). "
            "This is NOT the Django login password."
        ),
    )

    # --- DN configuration ---
    # The base DN is typically the same for all users; store it in settings.
    # Only the uid component varies, and that comes from user.username.

    class Meta:
        verbose_name = "LDAP User Profile"
        verbose_name_plural = "LDAP User Profiles"

    def __str__(self):
        return f"LDAP profile for {self.user.username} (uid={self.uid_number})"

    def save(self, *args, **kwargs):
        # Auto-populate gecos and home_directory if not set
        if not self.gecos:
            self.gecos = f"{self.user.first_name} {self.user.last_name}".strip()
        if not self.home_directory:
            base = getattr(settings, "LDAP_HOME_DIRECTORY_BASE", "/nfs/home")
            self.home_directory = f"{base}/{self.user.username}"
        super().save(*args, **kwargs)

    @property
    def dn(self):
        """Construct the full distinguished name from settings + username."""
        base_dn = getattr(
            settings,
            "LDAP_USER_BASE_DN",
            "ou=People,dc=hpsc,dc=tennessee,dc=edu",
        )
        return f"uid={self.user.username},{base_dn}"

    @property
    def object_classes(self):
        """Return the standard POSIX/LDAP object classes."""
        return [
            "person",
            "organizationalPerson",
            "inetOrgPerson",
            "posixAccount",
            "top",
            "shadowAccount",
            "hostObject",
        ]

    def to_ldif(self):
        """Export this user as an LDIF string."""
        lines = [f"dn: {self.dn}"]
        lines.append(f"loginShell: {self.login_shell}")

        for oc in self.object_classes:
            lines.append(f"objectClass: {oc}")

        lines.append(f"cn: {self.user.first_name}")
        lines.append(f"sn: {self.user.last_name}")
        lines.append(f"uid: {self.user.username}")
        lines.append(f"uidNumber: {self.uid_number}")
        lines.append(f"gidNumber: {self.gid_number}")
        lines.append(f"homeDirectory: {self.home_directory}")
        lines.append(f"gecos: {self.gecos}")

        if self.user.email:
            lines.append(f"mail: {self.user.email}")

        lines.append(f"shadowLastChange: {self.shadow_last_change}")
        lines.append(f"shadowMax: {self.shadow_max}")
        lines.append(f"shadowWarning: {self.shadow_warning}")

        for host in self.hosts.all():
            lines.append(f"host: {host.name}")

        if self.ldap_password:
            lines.append(f"userPassword:: {self.ldap_password}")

        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# Optional: auto-create a (minimal) LDAPUserProfile when a User is created.
# You may prefer to NOT do this and instead create profiles explicitly
# only for users who need LDAP accounts, since uid_number must be unique
# and meaningful. Uncomment if you want auto-creation with an allocator.
# ──────────────────────────────────────────────────────────────

# from coldfront_plugin_ldap_export.utils import allocate_next_uid
#
# @receiver(post_save, sender=User)
# def create_ldap_profile(sender, instance, created, **kwargs):
#     if created:
#         LDAPUserProfile.objects.create(
#             user=instance,
#             uid_number=allocate_next_uid(),
#             gid_number=getattr(settings, "LDAP_DEFAULT_GID", 3319),
#         )
