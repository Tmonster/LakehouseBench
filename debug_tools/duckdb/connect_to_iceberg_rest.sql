CREATE SECRET (
	TYPE S3,
	KEY_ID 'admin',
	SECRET 'password',
	ENDPOINT '127.0.0.1:9000',
	URL_STYLE 'path',
	USE_SSL 0
); 
ATTACH '' AS my_datalake (
	TYPE ICEBERG,
	CLIENT_ID 'admin',
	CLIENT_SECRET 'password',
	ENDPOINT 'http://127.0.0.1:8181'
);

