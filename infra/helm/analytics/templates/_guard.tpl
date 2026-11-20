{{- define "analytics.guard" -}}
{{- if .Values.knowi01Staged.enabled -}}
{{- if or (ne .Release.Namespace "knowi-analytics") (ne .Values.runtime.mode "production") (not .Values.runtime.hostBootRecoveryGate) (not .Values.authorityServer.enabled) (not .Values.privateCoreSource.enabled) (not .Values.roles.privacy.enabled) (not .Values.image.repository) (not .Values.network.databaseCIDR) -}}
{{- fail "knowi01 staged profile requires production guards, authority, private source, privacy, image repository and exact Analytics DB address" -}}
{{- end -}}
{{- if or .Values.migration.enabled .Values.ingress.enabled .Values.browserCollection.enabled .Values.coreTransfer.enabled .Values.catalog.enabled .Values.activityProjection.enabled .Values.activityDay.enabled .Values.activityCourse.enabled .Values.activityBoundaryCredit.enabled -}}
{{- fail "knowi01 staged profile must keep migration, public and user-facing paths disabled" -}}
{{- end -}}
{{- end -}}
{{- if not (kindIs "bool" .Values.runtime.hostBootRecoveryGate) -}}
{{- fail "runtime.hostBootRecoveryGate must be boolean" -}}
{{- end -}}
{{- if and .Values.runtime.hostBootRecoveryGate (or (ne .Values.runtime.mode "production") (not .Values.authorityServer.enabled)) -}}
{{- fail "Host-boot recovery gate requires the production runtime and its managed authority" -}}
{{- end -}}
{{- if not (kindIs "bool" .Values.browserCollection.enabled) -}}
{{- fail "browserCollection.enabled must be boolean" -}}
{{- end -}}
{{- if .Values.browserCollection.enabled -}}
{{- if or (ne .Values.runtime.mode "production") (not .Values.catalog.enabled) (not .Values.roles.privacy.enabled) .Values.network.coreIngress.cidr (not .Values.network.coreIngress.namespace) (not .Values.network.coreIngress.podLabels) -}}
{{- fail "Browser collection requires production, catalog, privacy and a selected Core namespace and Pod" -}}
{{- end -}}
{{- if not (and .Values.activityProjection.enabled .Values.activityDay.enabled .Values.activityCourse.enabled) -}}
{{- fail "Browser collection requires the activity, day and course projections" -}}
{{- end -}}
{{- end -}}
{{- $benchmarkNamespace := and (eq .Values.syntheticBenchmark.enabled true) (regexMatch "^knowi-analytics-bench-[0-9a-f]{32}$" .Release.Namespace) -}}
{{- if and (ne .Release.Namespace "knowi-analytics") (not $benchmarkNamespace) -}}
{{- fail "Analytics must be installed in the separately bootstrapped knowi-analytics namespace" -}}
{{- end -}}
{{- if and .Values.image.digest (not (regexMatch "^sha256:[0-9a-f]{64}$" .Values.image.digest)) -}}
{{- fail "Use an immutable sha256 image manifest digest" -}}
{{- end -}}
{{- if and $benchmarkNamespace (not .Values.image.digest) -}}
{{- fail "Synthetic benchmark requires an immutable image manifest digest" -}}
{{- end -}}
{{- if .Values.image.digest -}}
{{- range $role, $cfg := .Values.roles -}}
{{- if $cfg.imageTag -}}
{{- fail "Independent role tags cannot be combined with image.digest" -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if and .Values.migration.enabled (not (regexMatch "^a[0-9]{3}$" .Values.migration.revision)) -}}
{{- fail "An explicit Analytics migration revision is required" -}}
{{- end -}}
{{- if and .Values.trustManifest.configMapName (not (regexMatch "^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$" .Values.trustManifest.configMapName)) -}}
{{- fail "Use one valid ConfigMap name for the common public trust manifest" -}}
{{- end -}}
{{- if and .Values.fixtureCatalog.configMapName (not (regexMatch "^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$" .Values.fixtureCatalog.configMapName)) -}}
{{- fail "Use a valid ConfigMap name for the synthetic catalog fixture" -}}
{{- end -}}
{{- if and .Values.fixtureCatalog.configMapName (not .Values.fixtureCatalog.key) -}}
{{- fail "Synthetic catalog ConfigMap key must be specified" -}}
{{- end -}}
{{- $secrets := list .Values.roles.collector.secretName .Values.roles.processor.secretName .Values.roles.query.secretName .Values.migration.secretName -}}
{{- if .Values.roles.privacy.enabled -}}
{{- $secrets = append $secrets .Values.roles.privacy.secretName -}}
{{- if not .Values.privacyAuthority.secretName -}}
{{- fail "Privacy worker requires the shared authority Secret" -}}
{{- end -}}
{{- end -}}
{{- if and .Values.privacyAuthority.secretName (not .Values.privacyAuthority.key) -}}
{{- fail "Privacy authority Secret key must be specified" -}}
{{- end -}}
{{- if and .Values.privacyAuthority.secretName (has .Values.privacyAuthority.secretName $secrets) -}}
{{- fail "Privacy authority Secret must be distinct from database Secrets" -}}
{{- end -}}
{{- if ne (len (uniq $secrets)) (len $secrets) -}}
{{- fail "Use a distinct database Secret for each Analytics role" -}}
{{- end -}}
{{- $feature := or .Values.coreTransfer.enabled .Values.catalog.enabled .Values.activityProjection.enabled -}}
{{- $knownRevisions := list "a001" "a002" "a003" "a004" "a005" "a006" "a007" "a008" "a009" "a010" "a011" "a012" "a013" "a014" -}}
{{- if not (has .Values.migration.revision $knownRevisions) -}}
{{- fail "Analytics requires a known schema revision (a001 through a014)" -}}
{{- end -}}
{{- if and .Values.coreTransfer.proofEnabled (not .Values.coreTransfer.enabled) -}}
{{- fail "Transfer proof requires transfer" -}}
{{- end -}}
{{- if and $feature (or (not .Values.trustManifest.configMapName) (not .Values.privacyAuthority.secretName)) -}}
{{- fail "Feature rollout requires shared public trust and privacy authority" -}}
{{- end -}}
{{- if and $feature (not (regexMatch "^a[0-9]{3}$" .Values.migration.revision)) -}}
{{- fail "Feature rollout requires explicit schema revision" -}}
{{- end -}}
{{- $rev := atoi (trimPrefix "a" .Values.migration.revision) -}}
{{- if and .Values.coreTransfer.enabled (or (lt $rev 7) (not .Values.coreTransfer.statusSecretName) (not .Values.coreTransfer.namespacesConfigMapName) (not .Values.coreTransfer.queryNamespace)) -}}
{{- fail "Transfer requires a007, distinct status Secret, namespace map and query namespace" -}}
{{- end -}}
{{- if and .Values.coreTransfer.enabled (or (has .Values.coreTransfer.statusSecretName $secrets) (eq .Values.coreTransfer.statusSecretName .Values.privacyAuthority.secretName)) -}}
{{- fail "Transfer status Secret must be distinct" -}}
{{- end -}}
{{- if and .Values.coreTransfer.proofEnabled (lt $rev 9) -}}
{{- fail "Transfer proof requires a009" -}}
{{- end -}}
{{- if and .Values.catalog.enabled (or (lt $rev 8) (not .Values.catalog.namespace) (not .Values.coreTransfer.namespacesConfigMapName) .Values.fixtureCatalog.configMapName) -}}
{{- fail "Catalog requires a008, namespace map and exclusive catalog storage" -}}
{{- end -}}
{{- if and .Values.activityProjection.enabled (or (not (has .Values.migration.revision (list "a010" "a011" "a012" "a013" "a014"))) (not (regexMatch "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$" .Values.activityProjection.runId))) -}}
{{- fail "Projection requires a010 through a014 and one canonical dataset UUID" -}}
{{- end -}}
{{- $pollMs := toString .Values.activityProjection.processorPollMs -}}
{{- if or (not (regexMatch "^[0-9]{2,3}$" $pollMs)) (lt (atoi $pollMs) 50) (gt (atoi $pollMs) 500) -}}
{{- fail "activityProjection.processorPollMs must be an integer from 50 to 500" -}}
{{- end -}}
{{- if not (kindIs "bool" .Values.activityDay.enabled) -}}
{{- fail "activityDay.enabled must be boolean" -}}
{{- end -}}
{{- if and .Values.activityDay.enabled (or (not (has .Values.migration.revision (list "a011" "a012" "a013" "a014"))) (not .Values.activityProjection.enabled) (not .Values.roles.privacy.enabled) (not .Values.privacyAuthority.secretName)) -}}
{{- fail "Day projection requires a011 through a014, activity projection and Privacy" -}}
{{- end -}}
{{- if not (kindIs "bool" .Values.activityCourse.enabled) -}}
{{- fail "activityCourse.enabled must be boolean" -}}
{{- end -}}
{{- if and .Values.activityCourse.enabled (or (not (has .Values.migration.revision (list "a012" "a013" "a014"))) (not .Values.activityDay.enabled)) -}}
{{- fail "Course projection requires a012 through a014 and day projection" -}}
{{- end -}}
{{- if not (kindIs "bool" .Values.activityBoundaryCredit.enabled) -}}
{{- fail "activityBoundaryCredit.enabled must be boolean" -}}
{{- end -}}
{{- if and .Values.activityBoundaryCredit.enabled (or (not (has .Values.migration.revision (list "a013" "a014"))) (not .Values.activityCourse.enabled)) -}}
{{- fail "Boundary credit requires a013 or a014 and course projection" -}}
{{- end -}}
{{- if .Values.authorityServer.enabled -}}
{{- $a := .Values.authorityServer -}}
{{- $anchorExact := or (regexMatch "^([0-9]{1,3}\\.){3}[0-9]{1,3}/32$" $a.anchorCIDR) (regexMatch "^[0-9A-Fa-f:]+/128$" $a.anchorCIDR) -}}
{{- if or (ne .Values.runtime.mode "production") .Values.syntheticBenchmark.enabled .Values.fixtureCatalog.configMapName (not .Values.image.digest) (not $a.configSecretName) (not $a.journalClaimName) (ne $a.anchorBackend "postgres") (not $anchorExact) (not $a.coreNamespace) (not $a.corePodLabels) (not .Values.authorityClientTLS.secretName) -}}
{{- fail "Authority requires an immutable image, journal claim, exact PostgreSQL anchor, private config and client TLS Secrets and explicit Core selector" -}}
{{- end -}}
{{- if and (eq $a.anchorCIDR .Values.network.databaseCIDR) (not .Values.runtime.hostBootRecoveryGate) -}}
{{- fail "A shared database address requires the host-boot recovery gate" -}}
{{- end -}}
{{- end -}}
{{- if not (has .Values.runtime.mode (list "synthetic" "production")) -}}
{{- fail "runtime.mode must be synthetic or production" -}}
{{- end -}}
{{- if .Values.privateCoreSource.enabled -}}
{{- $s := .Values.privateCoreSource -}}
{{- $dbExact := or (regexMatch "^([0-9]{1,3}\\.){3}[0-9]{1,3}/32$" $s.databaseCIDR) (regexMatch "^[0-9A-Fa-f:]+/128$" $s.databaseCIDR) -}}
{{- $dbSelector := and $s.databaseNamespace $s.databasePodLabels -}}
{{- if or (and $s.databaseCIDR (or (not $dbExact) $s.databaseNamespace $s.databasePodLabels)) (and (not $s.databaseCIDR) (not $dbSelector)) -}}
{{- fail "Private Core source DB egress requires either one exact IP or a namespace and non-empty Pod selector" -}}
{{- end -}}
{{- if or (ne .Values.runtime.mode "production") (not $s.imageRepository) (not (regexMatch "^sha256:[0-9a-f]{64}$" $s.imageDigest)) (not $s.databaseSecretName) (not $s.hmacSecretName) (not $s.databaseCASecretName) (not $s.serverTLSSecretName) (not $s.expectedHost) (not $s.expectedDatabase) (not $s.expectedUser) (not (regexMatch "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$" $s.instanceId)) -}}
{{- fail "Private Core source requires production, immutable Core image, separate Secrets, DB identity and instance UUID" -}}
{{- end -}}
{{- if ne (len (uniq (list $s.databaseSecretName $s.hmacSecretName $s.databaseCASecretName $s.serverTLSSecretName))) 4 -}}
{{- fail "Private Core source DB, HMAC, DB CA and TLS Secrets must be distinct" -}}
{{- end -}}
{{- if or (ne .Values.coreDeletion.namespace .Release.Namespace) (ne (len .Values.coreDeletion.podLabels) 2) (ne (get .Values.coreDeletion.podLabels "app.kubernetes.io/name") "analytics-core-source") (ne (get .Values.coreDeletion.podLabels "app.kubernetes.io/instance") .Release.Name) (ne (int .Values.coreDeletion.port) (int .Values.internalTLS.port)) -}}
{{- fail "Private Core source requires Core deletion egress to select this release source Pod and HTTPS port" -}}
{{- end -}}
{{- end -}}
{{- if eq .Values.runtime.mode "production" -}}
{{- $d := .Values.coreDeletion -}}
{{- $tls := .Values.internalTLS -}}
{{- if or .Values.syntheticBenchmark.enabled .Values.fixtureCatalog.configMapName .Values.ingress.enabled (not .Values.network.coreIngress.enabled) (not $tls.enabled) (not $tls.proxyImageRepository) (not (regexMatch "^sha256:[0-9a-f]{64}$" $tls.proxyImageDigest)) (not $tls.collectorSecretName) (not $tls.querySecretName) (eq $tls.collectorSecretName $tls.querySecretName) (not .Values.image.digest) (not .Values.trustManifest.configMapName) (not .Values.privacyAuthority.secretName) (not .Values.authorityClientTLS.secretName) (not $d.sourceSecretName) (not $d.statusSecretName) (not $d.relaySecretName) (not $d.tlsSecretName) (not $d.namespace) (not $d.podLabels) -}}
{{- fail "Production requires immutable image, trust, authority, deletion source/status/relay and TLS Secrets and explicit Core selector; fixtures are forbidden" -}}
{{- end -}}
{{- $private := concat $secrets (list .Values.privacyAuthority.secretName $d.sourceSecretName $d.statusSecretName $d.relaySecretName) -}}
{{- if ne (len (uniq $private)) (len $private) -}}
{{- fail "Database, authority, deletion source, status and relay Secrets must be distinct" -}}
{{- end -}}
{{- end -}}
{{- end -}}
