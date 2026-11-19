"""Proxy routing, validation and credential isolation, with no network calls."""
import json,os,unittest
from types import SimpleNamespace
from unittest.mock import patch
import collection


class ProxyTests(unittest.TestCase):
    def config(self, values):
        with patch.dict(os.environ, values, clear=True):return collection.proxy_config()

    def test_default_direct_ignores_unused_credentials(self):
        self.assertIsNone(self.config({'juliang_proxy_pass':'unused','op_proxy_url_http':'invalid'}))

    def test_juliang_and_encoded_home_credentials(self):
        result=self.config({'XIN_CRAWLER_PROXY':'juliang','juliang_proxy_host':'proxy.example.test',
            'juliang_proxy_port':'1234','juliang_proxy_user':'test-user','juliang_proxy_pass':'test-pass'})
        self.assertEqual(result,{'server':'http://proxy.example.test:1234','username':'test-user','password':'test-pass'})
        result=self.config({'XIN_CRAWLER_PROXY':'openwrt','op_proxy_url_http':'http://u%40name:p%3Ass%23@home.example.test:4567',
            'op_proxy_port':'misnamed-password','op_proxy_url_socks5':'socks5h://other:secret@wrong.example.test:4444'})
        self.assertEqual(result,{'server':'http://home.example.test:4567','username':'u@name','password':'p:ss#'})

    def test_socks_dns_and_auth_limit(self):
        result=self.config({'XIN_CRAWLER_PROXY':'custom','XIN_CRAWLER_PROXY_SERVER':'socks5h://[::1]:1080'})
        self.assertEqual(result,{'server':'socks5://[::1]:1080'})
        with self.assertRaisesRegex(ValueError,'不支持带认证的SOCKS5'):
            self.config({'XIN_CRAWLER_PROXY':'openwrt','op_proxy_url_socks5':'socks5h://user:pass@home.example.test:1080'})

    def test_bad_configuration_fails_closed_without_leaking_secrets(self):
        for server in ('http://user:secret-value@host:99999','ftp://host:21','http://host:80/path',
                       'http://user:secret-value@host:80?q=secret-value','http://user@host:80'):
            with self.subTest(server=server):
                with self.assertRaises(ValueError) as error:
                    self.config({'XIN_CRAWLER_PROXY':'custom','XIN_CRAWLER_PROXY_SERVER':server})
                self.assertNotIn('secret-value',str(error.exception))
        with self.assertRaises(ValueError):self.config({'XIN_CRAWLER_PROXY':'not-a-route'})

    def test_selected_config_roundtrip_and_worker_environment_isolation(self):
        values={'XIN_CRAWLER_PROXY':'juliang','juliang_proxy_host':'proxy.example.test','juliang_proxy_port':'1234',
          'juliang_proxy_user':'test-user','juliang_proxy_pass':'test-pass','TYAPP_DATABASE_URL':'private-db',
          'TYAPP_S3_SECRET_ACCESS_KEY':'private-s3','op_proxy_url_http':'http://other:secret@home.example.test:4567'}
        result=SimpleNamespace(returncode=0,stdout=json.dumps({'status':'restricted','message':'synthetic'}))
        with patch.dict(os.environ,values,clear=True),patch.object(collection.subprocess,'run',return_value=result) as run:
            collection.collect('https://people.rednet.cn/front/messages/detail?id=1234')
        args,kwargs=run.call_args;child=kwargs['env']
        self.assertNotIn('test-pass',str(args));self.assertNotIn('op_proxy_url_http',child)
        self.assertNotIn('TYAPP_DATABASE_URL',child);self.assertNotIn('TYAPP_S3_SECRET_ACCESS_KEY',child)
        cfg=self.config({'XIN_CRAWLER_PROXY_CONFIG':child['XIN_CRAWLER_PROXY_CONFIG']})
        self.assertEqual(cfg['server'],'http://proxy.example.test:1234')
        self.assertEqual(cfg['password'],'test-pass')

    def test_configured_proxy_network_failure_never_retries_direct(self):
        with patch.dict(os.environ,{'XIN_CRAWLER_PROXY':'custom','XIN_CRAWLER_PROXY_SERVER':'http://host:80'},clear=True), \
             patch.object(collection.subprocess,'run',side_effect=OSError('synthetic')) as run:
            with self.assertRaisesRegex(ValueError,'采集进程未能返回结果'):
                collection.collect('https://people.rednet.cn/front/messages/detail?id=1234')
            self.assertEqual(run.call_count,1)


if __name__=='__main__':unittest.main()
