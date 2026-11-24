"""Resolve the two OneView entries without trusting public proxy headers."""
import ipaddress
import os
import re


def _proxy_networks(default=''):
    configured = os.environ.get('XIN_TRUSTED_PROXY_CIDRS', default).split(',')
    return tuple(ipaddress.ip_network(cidr.strip()) for cidr in configured if cidr.strip())


def route_base(headers, peer):
    trusted = any(ipaddress.ip_address(peer) in network for network in _proxy_networks('127.0.0.0/8,::1/128'))
    value = headers.get('X-OneView-Route-Base', '/') if trusted else '/'
    allowed = ['/', '/apps/' + os.environ.get('ONEVIEW_APP_SLUG', 'bfzx-yqda')]
    if value not in allowed or not re.fullmatch(r'/|/apps/[a-z0-9-]+', value):
        raise ValueError('Invalid route base')
    return value


def login_client_ip(headers, peer):
    networks = _proxy_networks()
    if not any(ipaddress.ip_address(peer) in network for network in networks):
        return peer

    forwarded = headers.get_all('X-Forwarded-For', [])
    if len(forwarded) != 1:
        return peer
    # Stop at the first non-proxy hop. Entries to its left may be client-supplied.
    for value in reversed(forwarded[0].split(',')):
        try:
            address = ipaddress.ip_address(value.strip())
        except ValueError:
            return peer
        if getattr(address, 'scope_id', None) is not None:
            return peer
        if not any(address in network for network in networks):
            return str(address)
    return peer


def cookie_name():
    return 'xin_' + os.environ.get('ONEVIEW_APP_SLUG', 'bfzx-yqda').replace('-', '_') + '_session'


def cookie_path(base):
    return base.rstrip('/') + '/'
