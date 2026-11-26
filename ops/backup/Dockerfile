FROM postgres:16-alpine
RUN apk add --no-cache rclone openssl
COPY backup.sh restore.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/backup.sh /usr/local/bin/restore.sh
ENTRYPOINT ["backup.sh"]
CMD ["daemon"]
