"""Resolve the two OneView entries without trusting public proxy headers."""
import ipaddress
import os
import re


def route_base(headers, peer):
    networks = os.environ.get('XIN_TRUSTED_PROXY_CIDRS', '127.0.0.0/8,::1/128').split(',')
    trusted = any(ipaddress.ip_address(peer) in ipaddress.ip_network(n.strip()) for n in networks if n.strip())
    value = headers.get('X-OneView-Route-Base', '/') if trusted else '/'
    allowed = ['/', '/apps/' + os.environ.get('ONEVIEW_APP_SLUG', 'bfzx-yqda')]
    if value not in allowed or not re.fullmatch(r'/|/apps/[a-z0-9-]+', value):
        raise ValueError('Invalid route base')
    return value


def cookie_name():
    return 'xin_' + os.environ.get('ONEVIEW_APP_SLUG', 'bfzx-yqda').replace('-', '_') + '_session'


def cookie_path(base):
    return base.rstrip('/') + '/'
