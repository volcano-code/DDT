# Public distribution and execution boundaries

This public repository must not contain proprietary DDT installers or JARs,
derived proprietary class patches, customer or accounting data, runtime sessions,
credentials, licenses, private signing keys, fonts, or raw client logs.

The public platform workflow uses self-written Java and one hash-pinned, strictly
verified public Eclipse dependency. It renders only a local synthetic page. It
never loads the proprietary client, uses vendor accounts, calls a model, performs
accounting writes, or creates a production deployment. No secrets are required;
repository permission is contents:read, checkout credentials are not persisted,
and neither AppArmor nor the browser sandbox is disabled. System package changes
apply only to the ephemeral GitHub-hosted test VM.

Any prohibited file or likely credential finding blocks publication/execution.
Do not paste sensitive evidence in public issues. A successful heuristic scan is
not a certification. Signature validation does not include online revocation.

Proprietary-client acceptance remains a separate, explicitly authorized private
procedure. Public platform success is not vendor-login, real-export, target-GPU,
Ubuntu-host-policy, or production acceptance. Do not upload the complete previous
internal evidence archive to this repository without a new review.
