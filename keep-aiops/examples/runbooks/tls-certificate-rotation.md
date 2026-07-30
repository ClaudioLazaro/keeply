# TLS Certificate Rotation (Ingress)

## Symptoms

- Browsers and clients report `certificate expired` or
  `x509: certificate has expired or is not yet valid` on ingress endpoints.
- Prometheus blackbox exporter fires `SslCertificateExpiry` or probe
  failures with TLS handshake errors.

## Diagnosis

1. Inspect the served certificate:
   `echo | openssl s_client -connect api.example.com:443 -servername api.example.com 2>/dev/null | openssl x509 -noout -dates -issuer`
2. Check the cert-manager Certificate resource:
   `kubectl get certificate -n ingress` — look for `Ready=False` and read
   `kubectl describe certificate <name>` for ACME order failures.
3. Verify the referenced secret exists and is current:
   `kubectl get secret <tls-secret> -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -noout -dates`

## Mitigation

1. Force renewal: `kubectl delete certificaterequest --all -n ingress` and
   annotate the Certificate with `cert-manager.io/issue-temporary-certificate`
   if the CA is rate-limiting.
2. If cert-manager is down, renew manually with certbot and re-upload the
   secret: `kubectl create secret tls <tls-secret> --cert=fullchain.pem --key=privkey.pem --dry-run=client -o yaml | kubectl apply -f -`
3. Restart ingress controller pods only if they cached the old secret.

## Prevention

- Alert 14 days before expiry via blackbox exporter.
- Keep ACME staging and prod issuers separate to avoid rate-limit surprises
  during DR drills.
