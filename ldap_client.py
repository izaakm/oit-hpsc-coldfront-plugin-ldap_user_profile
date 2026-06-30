import ldap3

from typing import Optional, Dict, List, Any


class ObjectHelper:
    def __init__(
        self,
        connection,
        object_class,
        base
    ):
        # obj_inetorgperson = ObjectDef('inetOrgPerson', conn)
        # r = Reader(conn, obj_inetorgperson, 'dc=demo1,dc=freeipa,dc=org')
        self.object_def = ldap3.ObjectDef(object_class, connection)
        self.reader = ldap3.Reader(connection, self.object_def, base)

    def search(self):
        self.reader.search()


class LDAPClient:
    def __init__(
        self,
        host=None,
        port=None,
        use_ssl=False,
        allowed_referral_hosts=None,
        get_info=ldap3.ALL,
        tls=None,
        formatter=None,
        connect_timeout=5.0,
        mode='IP_V6_PREFERRED',
        validator=None,
        password=None,
        # base_dn=None,
        search_base=None,
        user=None,
        # people_ou=None,
        # groups_dn=None,
        # restricted_countries=None,
        read_only=True,
        lazy=True,
    ):
        ## These are only used to set up the server instance. ---8<---
        # self.host = host
        # self.port = port
        # self.use_ssl = use_ssl
        # self.allowed_referral_hosts = allowed_referral_hosts
        # self.get_info = get_info
        # self.tls = tls
        # self.formatter = formatter
        # self.connect_timeout = connect_timeout
        # self.mode = mode
        # self.validator = validator
        ## --->8---

        self.password = password  # Probably don't store it, get from env every time?
        self.search_base = search_base
        self.user = user
        # self.people_ou = people_ou
        # self.groups_dn = groups_dn
        # self.restricted_countries = restricted_countries

        self.server = ldap3.Server(
            host,
            port=port,
            use_ssl=use_ssl,
            allowed_referral_hosts=allowed_referral_hosts,
            get_info=get_info,
            tls=tls,
            formatter=formatter,
            connect_timeout=connect_timeout,
            mode=mode,
            validator=validator
        )

        # client_strategy=SAFE_SYNC: the request is sent and the connection waits until the
        # response is received. Each operation returns a tuple of 4 elements:
        # status, result, response, request. This strategy is thread-safe.
        self.connection = ldap3.Connection(
            self.server,
            user=self.user,
            password=self.password,
            auto_bind=False,
            # client_strategy="SAFE_SYNC", # wait for response, thread-safe  ~> NOT WORKING????
            client_strategy="SYNC", # works .... why???
            read_only=read_only,
            lazy=lazy                    # open and bind the connection only when an operation is performed
        )

        self.objectclasses = {}

    def connect(self):
        raise NotImplementedError('Use the context manager instead to automatically open/bind and unbind.')

    def disconnect(self):
        self.unbind()

    def unbind(self):
        '''
        Just for peace of mind.
        '''
        self.connection.unbind()

    def add_objectdef(self, object_class, base):
        if base is None and self.search_base is None:
            raise ValueError('`base` is required.')
        elif base is None:
            base = self.search_base

        if isinstance(object_class, list):
            key = '_'.join(object_class)
        elif isinstance(object_class, str):
            key = object_class
        else:
            raise ValueError(f'`object_class` must be str or list, you gave {type(object_class)}')

        with self.connection as conn:
            self.objectclasses[key] = ObjectHelper(self.connection, object_class=object_class, base=base)

    def get(self, object_class):
        if isinstance(object_class, list):
            key = '_'.join(object_class)
        elif isinstance(object_class, str):
            key = object_class
        else:
            raise ValueError(f'`object_class` must be str or list, you gave {type(object_class)}')
        return self.objectclasses.get(key)

    def search(self):
        pass

    def create_user(self):
        pass

    def add_user_to_group(self):
        pass

    @staticmethod
    def hash_password():
        pass


class ISAACLDAP(LDAPClient):

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('read_only', False)
        super().__init__(*args, **kwargs)
        self._groups = None
        self._groups_dn = None
        self._users = None
        self._people_ou = None
        self._restricted_countries = []

    @property
    def users(self):
        return self._users

    @property
    def groups(self):
        return self._groups

    def update_users(self):
        '''
        Initialize or update the ObjectDef for "users".
        '''
        pass

    def update_groups(self):
        '''
        Initialize or update the objectDef for "groups".
        '''
        pass

    def get_max_uidnumber(
        self,
        search_base=None,
        search_filter='(uidNumber=*)',
        attributes=['uidNumber'],
    ):
        if search_base is None:
            search_base = self.search_base
        with self.connection as conn:
            conn.search(
                search_base=search_base,
                search_filter=search_filter,
                size_limit=0,
                attributes=attributes,
            )
            entries = conn.entries
        uids = []
        for entry in entries:
            # Reminder: Attribute values are lists, e.g., `{'uidNumber': [999]}`
            uids.extend(entry.entry_attributes_as_dict.get('uidNumber',[]))
        return max(uids)


class ISAACUser:
    def __init__(
            self,
            dn=None,
            object_class=[
                'top',
                'person',
                'organizationalPerson',
                'inetOrgPerson',
                'shadowAccount',
                'posixAccount'
            ],
            attributes=None,
            controls=None,
            parent='ou=People,dc=hpsc,dc=tennessee,dc=edu'
        ):
        '''
        See https://ldap3.readthedocs.io/en/latest/add.html#

        dn:
            distinguished name of the object to add
        object_class:
            class name of the attribute to add, can be a string containing a
            single value or a list of strings
            (The object_class parameter is a shortcut for specify a sequence of
            object classes. You can specify the object classes in the
            attributes parameter too.)
            * Use this to store the default object classes.
        attributes:
            a dictionary in the form {‘attr1’: ‘val1’, ‘attr2’: ‘val2’, …} or {‘attr1’: [‘val1’, ‘val2’, …], …} for multivalued attributes
        controls:
            additional controls to send with the request
        '''
        # [TODO] If a 'dn' is given, verify that it matches the expected format, then drop it.
        self._dn = dn

        # Store it in the attributes.
        # self._object_class = object_class

        # [IN_PROGRESS] Check for required attributes. Which attributes are required?
        missing = []
        for attr in ['uid', 'cn', 'sn', 'mail', 'userPassword']:
            if attr not in attributes:
                missing.append(attr)
        if missing:
            raise ValueError(f'User is missing {len(missing)} required attribute(s): {missing}')

        self._attributes = {**attributes}

        # Object class posixAccount requires these (prob in the schema???).
        # ['uidNumber', 'gidNumber', 'homeDirectory']
        if 'uidNumber' not in self.attributes:
            self.attributes['uidNumber'] = 99999
        if 'gidNumber' not in self.attributes:
            self.attributes['gidNumber'] = self.attributes['uidNumber']
        if 'homeDirectory' not in self.attributes:
            self.attributes['homeDirectory'] = f'/home/{self.attributes["uid"]}'

        # Deal with object_class, which can be:
        # - a str, list, or None
        # - passed explicitely or as part of attributes
        if isinstance(object_class, (list,None)):
            self._object_class = object_class
        elif isinstance(object_class, str):
            self._object_class = [object_class]
        else:
            raise ValueError(f'Unknown type of object class: {object_class} ({type(object_class)})')

        if 'object_class' in self._attributes:
            tmp = self._attributes.pop('object_class')
            if tmp is None:
                pass
            elif isinstance(tmp, list):
                self._object_class.extend(tmp)
            else:
                self._object_class.append(tmp)

        self._controls = controls

        self.parent = parent

    def __repr__(self):
        '''
        The __repr__ could be used to recreate the object from `eval(item.__repr__())`.
        '''
        return (
            f"{self.__class__.__name__}"
            f"(dn={self.dn.__repr__()},"
            f"object_class={self.object_class},"
            f"attributes={self.attributes},"
            f"controls={self.controls},"
            f"parent={self.parent.__repr__()})"
        )

    def __getattr__(self, name):
        if name in self._attributes:
            return self._attributes[name]
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    @property
    def relative_distinguished_name(self):
        '''
        > dn: cn=John Doe,dc=example,dc=com
        >
        > "dn" is the distinguished name of the entry; it is neither an
        > attribute nor a part of the entry. "cn=John Doe" is the entry's RDN
        > (Relative Distinguished Name), and "dc=example,dc=com" is the DN of
        > the parent entry, where "dc" denotes 'Domain Component'.  ~ Wikipedia
        '''
        return f'uid={self.uid}'

    @property
    def distinguished_name(self):
        return f'{self.relative_distinguished_name},{self.parent}'

    @property
    def dn(self):
        return self.distinguished_name

    @property
    def object_class(self):
        return self._object_class

    @property
    def attributes(self):
        return self._attributes

    @property
    def a(self):
        return self.attributes

    @property
    def controls(self):
        return self._controls

    # @property
    # def parent(self):
    #     return self._parent

# END
