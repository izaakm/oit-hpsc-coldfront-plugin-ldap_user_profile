"""
coldfront_plugin_ldap_export/admin.py

Register LDAPUserProfile and LDAPHost with Django's admin site.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from coldfront.plugins.ldap_user_profile.models import LDAPHost, LDAPUserProfile


# ──────────────────────────────────────────────────────────────
# LDAPHost
# ──────────────────────────────────────────────────────────────

@admin.register(LDAPHost)
class LDAPHostAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


# ──────────────────────────────────────────────────────────────
# LDAPUserProfile (standalone)
# ──────────────────────────────────────────────────────────────

@admin.register(LDAPUserProfile)
class LDAPUserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "uid_number",
        "gid_number",
        "home_directory",
        "login_shell",
        "get_dn",
    )
    list_filter = ("login_shell", "gid_number", "hosts")
    search_fields = (
        "user__username",
        "user__first_name",
        "user__last_name",
        "user__email",
        "uid_number",
    )
    readonly_fields = ("get_dn", "get_object_classes")
    filter_horizontal = ("hosts",)  # nicer widget for M2M

    fieldsets = (
        (None, {
            "fields": ("user",),
        }),
        ("POSIX Account", {
            "fields": (
                "uid_number",
                "gid_number",
                "home_directory",
                "login_shell",
                "gecos",
            ),
        }),
        ("Shadow Password", {
            "fields": (
                "shadow_last_change",
                "shadow_max",
                "shadow_warning",
            ),
            "classes": ("collapse",),  # collapsed by default
        }),
        ("Host Access", {
            "fields": ("hosts",),
        }),
        ("LDAP Password", {
            "fields": ("ldap_password",),
            "classes": ("collapse",),
        }),
        ("Computed (read-only)", {
            "fields": ("get_dn", "get_object_classes"),
        }),
    )

    @admin.display(description="DN")
    def get_dn(self, obj):
        return obj.dn

    @admin.display(description="Object Classes")
    def get_object_classes(self, obj):
        return ", ".join(obj.object_classes)


# ──────────────────────────────────────────────────────────────
# Inline: show LDAPUserProfile on the User change page
# ──────────────────────────────────────────────────────────────

class LDAPUserProfileInline(admin.StackedInline):
    model = LDAPUserProfile
    can_delete = False
    verbose_name = "LDAP Profile"
    verbose_name_plural = "LDAP Profile"
    filter_horizontal = ("hosts",)

    fieldsets = (
        ("POSIX Account", {
            "fields": (
                "uid_number",
                "gid_number",
                "home_directory",
                "login_shell",
                "gecos",
            ),
        }),
        ("Shadow Password", {
            "fields": (
                "shadow_last_change",
                "shadow_max",
                "shadow_warning",
            ),
            "classes": ("collapse",),
        }),
        ("Host Access", {
            "fields": ("hosts",),
        }),
        ("LDAP Password", {
            "fields": ("ldap_password",),
            "classes": ("collapse",),
        }),
    )


# Unregister the default UserAdmin and re-register with the inline
admin.site.unregister(User)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = BaseUserAdmin.inlines + tuple([LDAPUserProfileInline])
