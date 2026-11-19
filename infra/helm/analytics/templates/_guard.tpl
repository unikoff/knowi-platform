{{- define "analytics.guard" -}}
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
{{- end -}}
