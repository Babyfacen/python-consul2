import collections
import json
import os
import platform
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import uuid

import py
import pytest
import requests

collect_ignore = []
sys.path.insert(0,
                os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
                )
if sys.version_info[0] < 3:
    p = os.path.join(os.path.dirname(__file__), 'test_aio.py')
    collect_ignore.append(p)
    p = os.path.join(os.path.dirname(__file__), 'test_aio_acl.py')
    collect_ignore.append(p)

if sys.version_info[0] < 3:
    p = os.path.join(os.path.dirname(__file__), 'test_twisted.py')
    collect_ignore.append(p)
    p = os.path.join(os.path.dirname(__file__), 'test_twisted_acl.py')
    collect_ignore.append(p)
    p = os.path.join(os.path.dirname(__file__), 'test_tornado.py')
    collect_ignore.append(p)
    p = os.path.join(os.path.dirname(__file__), 'test_tornado_acl.py')
    collect_ignore.append(p)


def get_free_ports(num, host=None):
    if not host:
        host = '127.0.0.1'
    sockets = []
    ret = []
    for _ in range(num):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind((host, 0))
        ret.append(s.getsockname()[1])
        sockets.append(s)
    for s in sockets:
        s.close()
    return ret


def start_consul_instance(acl_master_token=None, acl_agent_token=None):
    """
    starts a consul instance. if acl_master_token is None, acl will be disabled
    for this server, otherwise it will be enabled and the master token will be
    set to the supplied token

    returns: a tuple of the instances process object and the http port the
             instance is listening on
    """
    ports = dict(zip(
        ['http', 'serf_lan', 'serf_wan', 'server', 'dns'],
        get_free_ports(4) + [-1]))

    config = dict(ports=ports,
                  performance={'raft_multiplier': 1},
                  enable_script_checks=True)
    config['datacenter'] = 'dc1'
    config['primary_datacenter'] = 'dc1'
    params = []

    if acl_master_token or acl_agent_token:
        config['acl'] = {
            "enabled": True,
            "default_policy": "deny",
            "enable_token_persistence": True,
            "tokens": {
                "master": acl_master_token,
                "agent": acl_agent_token
            }
        }
        token = acl_master_token or acl_agent_token
        if token:
            params.append(('token', token))

    tmpdir = py.path.local(tempfile.mkdtemp())
    tmpdir.join('config.json').write(json.dumps(config))
    tmpdir.chdir()

    (system, node, release, version, machine, processor) = platform.uname()
    if system == 'Darwin':
        postfix = 'osx'
    else:
        postfix = 'linux64'
    bin = os.path.join(os.path.dirname(__file__), 'consul.' + postfix)
    command = '{bin} agent -dev' \
              ' -bind=127.0.0.1' \
              ' -config-dir=.'
    command = command.format(bin=bin).strip()
    command = shlex.split(command)
    with open('/dev/null', 'w') as devnull:
        p = subprocess.Popen(
            command, stdout=devnull, stderr=devnull)

    # wait for consul instance to bootstrap
    base_uri = 'http://127.0.0.1:%s/v1/' % ports['http']

    while True:
        time.sleep(0.1)
        try:
            response = requests.get(base_uri + 'status/leader')
        except requests.ConnectionError:
            continue
        if response.text.strip() != '""':
            break

    requests.put(base_uri + 'agent/service/register',
                 params=params,
                 data='{"name": "foo"}')

    while True:
        response = requests.get(base_uri + 'health/service/foo', params=params)
        if response.text.strip() != '[]':
            break
        time.sleep(0.1)

    requests.put(base_uri + 'agent/service/deregister/foo')
    # phew
    time.sleep(3)
    return p, ports['http']


def clean_consul(port):
    # remove all data from the instance, to have a clean start
    base_uri = 'http://127.0.0.1:%s/v1/' % port
    requests.delete(base_uri + 'kv/', params={'recurse': 1})
    services = requests.get(base_uri + 'agent/services').json().keys()
    for s in services:
        requests.put(base_uri + 'agent/service/deregister/%s' % s)


@pytest.fixture(scope="module")
def consul_instance():
    p, port = start_consul_instance()
    yield port
    p.terminate()


@pytest.fixture
def consul_port(consul_instance):
    port = consul_instance
    yield port
    clean_consul(port)


@pytest.fixture(scope="module")
def acl_consul_instance():
    # acl_master_token = uuid.uuid4().hex
    acl_master_token = str(uuid.uuid4())
    p, port = start_consul_instance(acl_master_token=acl_master_token)
    yield port, acl_master_token
    p.terminate()


@pytest.fixture
def acl_consul(acl_consul_instance):
    ACLConsul = collections.namedtuple('ACLConsul', ['port', 'token'])
    port, token = acl_consul_instance
    yield ACLConsul(port, token)
    clean_consul(port)
