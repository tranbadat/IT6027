FROM httpd:2.4-alpine

COPY waf-vhost.conf /usr/local/apache2/conf/extra/waf-vhost.conf
COPY htdocs/ /usr/local/apache2/htdocs/

RUN mkdir -p /var/log/waf/apache \
    && echo 'Include conf/extra/waf-vhost.conf' >> /usr/local/apache2/conf/httpd.conf \
    && httpd -t
