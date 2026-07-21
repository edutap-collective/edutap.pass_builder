# assets

Static files that ship inside the application image (`Dockerfile` runs
`COPY assets /app/assets`).

## `wwdr-g4.pem`

Apple's **Worldwide Developer Relations (WWDR) intermediate certificate**,
generation G4. It chains a pass-signing certificate up to Apple's root CA and
is required to sign every `.pkpass` file.

This certificate is **public**. Apple publishes it for anyone to download; it
contains no private key and no secret material. It is committed to the
repository so the Docker image is self-contained and `Settings.wwdr_certificate_path`
(default `assets/wwdr-g4.pem`) resolves without any extra provisioning step.

Details:

- Subject: `CN=Apple Worldwide Developer Relations Certification Authority, OU=G4, O=Apple Inc., C=US`
- Issuer: `CN=Apple Root CA, OU=Apple Certification Authority, O=Apple Inc., C=US`
- Valid: 2020-12-16 to 2030-12-10

## Rotating the certificate

Apple occasionally issues a new WWDR generation.
When that happens, an operator must replace this file:

1. Download the current WWDR certificate from
   [Apple PKI](https://www.apple.com/certificateauthority/) (choose the
   "Worldwide Developer Relations - G⟨n⟩" certificate for the Pass Type ID
   / Apple Wallet chain).
2. Convert it to PEM if Apple ships it as DER:

   ```shell
   openssl x509 -inform der -in AppleWWDRCAG⟨n⟩.cer -out wwdr-g⟨n⟩.pem
   ```

3. Replace `assets/wwdr-g4.pem` (or point
   `EDUTAP_PASS_BUILDER_WWDR_CERTIFICATE_PATH` at a differently named file)
   and rebuild the image.
4. Re-sign a test pass for every active Apple credential set: this file is
   read at render time (see `RenderService`, which loads
   `Settings.wwdr_certificate_path` and passes it to
   `PkPass.sign_direct(private_key, certificate, wwdr)`), so a mismatched
   generation only surfaces as a signing failure at the next render, not at
   credential install time.
