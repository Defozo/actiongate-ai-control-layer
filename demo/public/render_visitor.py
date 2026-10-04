"""Add bounded public examples while preserving the private operator gateway."""
import argparse
from pathlib import Path

MARKER='# ActionGate public visitor entry'

def quoted_asset(path):
    text=path.read_text(encoding='utf8')
    if '$' in text or len(text.encode())>3800:
        raise ValueError('Static response must be bounded and contain no nginx variables')
    return "'"+text.replace('\\','\\\\').replace("'","\\'")+"'"

def render(base, directory):
    if MARKER in base:
        raise ValueError('Use the preserved operator-only baseline to render once')
    required=['auth_basic "ActionGate jury demo";', 'auth_basic_user_file /etc/nginx/jury.htpasswd;', 'location / {']
    if any(x not in base for x in required):
        raise ValueError('Unexpected operator gateway baseline')
    # Keep the same exact Origin translation as the existing operator proxy.
    origin=[s.strip() for s in base.splitlines() if s.strip().startswith('proxy_set_header Origin ')][0]
    proxy='''
            set $demo_edge "edge:8080";
            proxy_http_version 1.1;
            proxy_set_header Host 127.0.0.1;
            ORIGIN_HEADER
            proxy_set_header Authorization "";
            proxy_set_header Cookie "actiongate_session=$cookie_actiongate_public_session";
            proxy_set_header Connection "";
            proxy_set_header X-Forwarded-Proto https;
            proxy_buffering off;
            proxy_request_buffering on;
            proxy_read_timeout 1900s;
            proxy_send_timeout 1900s;
            proxy_next_upstream off;
            add_header Cache-Control "no-store" always;
'''.replace('ORIGIN_HEADER',origin)
    blocks=[MARKER]
    for route,file,mime in [('/', 'visitor.html','text/html; charset=utf-8'),('/public-demo.js','visitor.js','application/javascript; charset=utf-8'),('/public-demo.css','visitor.css','text/css; charset=utf-8')]:
        blocks.append('''        location = ROUTE {
            auth_basic off;
            if ($request_method !~ ^(GET|HEAD)$) { return 405; }
            default_type "MIME";
            add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'" always;
            add_header X-Content-Type-Options nosniff always;
            add_header Referrer-Policy no-referrer always;
            add_header Cache-Control "no-store" always;
            return 200 BODY;
        }
'''.replace('ROUTE',route).replace('MIME',mime).replace('BODY',quoted_asset(directory/file)))
    for route in ['/public-api/health','/health','/health/ready']:
        blocks.append('''        location = ROUTE {
            auth_basic off;
            if ($request_method !~ ^(GET|HEAD)$) { return 405; }
            proxy_pass http://$demo_edge/health/ready;
PROXY
        }
'''.replace('ROUTE',route).replace('PROXY',proxy))
    blocks.append('''        location = /public-api/session {
            auth_basic off;
            if ($request_method != POST) { return 405; }
            limit_req zone=visitor_sessions burst=4 nodelay;
            limit_req_status 429;
            proxy_pass http://$demo_edge/api/demo/session;
            proxy_set_header Content-Type application/json;
            proxy_set_body '{"role":"analyst","tenant":"synthetic_test_tenant"}';
            proxy_hide_header Set-Cookie;
            add_header Set-Cookie "actiongate_public_session=$upstream_cookie_actiongate_session; Path=/public-api/; Max-Age=3600; Secure; HttpOnly; SameSite=Strict";
PROXY
        }
'''.replace('PROXY',proxy))
    for scenario in ['legal','injection','cross_tenant','pii']:
        blocks.append('''        location = /public-api/workflow/SCENARIO {
            auth_basic off;
            if ($request_method != POST) { return 405; }
            limit_req zone=visitor_workflows burst=3 nodelay;
            limit_req_status 429;
            limit_conn visitor_active 1;
            limit_conn_status 429;
            proxy_pass http://$demo_edge/api/demo/workflow;
            proxy_set_header Content-Type application/json;
            proxy_set_body '{"scenario":"SCENARIO"}';
PROXY
        }
'''.replace('SCENARIO',scenario).replace('PROXY',proxy))
    blocks.append('''        location ^~ /public-api/ {
            auth_basic off;
            return 404;
        }
        location = /operator {
            set $operator_edge "edge:8080";
            proxy_pass http://$operator_edge/;
            proxy_http_version 1.1;
            proxy_set_header Host 127.0.0.1;
            ORIGIN_HEADER
            proxy_set_header Authorization "";
            proxy_set_header Connection "";
            proxy_set_header X-Forwarded-Proto https;
            proxy_buffering off;
            proxy_cookie_flags actiongate_session secure httponly samesite=strict;
        }
'''.replace('ORIGIN_HEADER',origin))
    base=base.replace('    server {','''    limit_req_zone $binary_remote_addr zone=visitor_sessions:1m rate=20r/m;
    limit_req_zone $server_name zone=visitor_workflows:1m rate=6r/m;
    limit_conn_zone $server_name zone=visitor_active:1m;
    server {''',1)
    return base.replace('        location / {','\n'.join(blocks)+'        location / {',1)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--base-config',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    rendered=render(args.base_config.read_text(encoding='utf8'),Path(__file__).parent)
    args.output.write_text(rendered,encoding='utf8')
    print('Rendered bounded public entry; operator authentication retained')
