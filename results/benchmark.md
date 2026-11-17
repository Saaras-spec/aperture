# Aperture Real-World Migration Benchmark

*Generated at: 2026-11-17T13:01:12.383490+00:00*

## Executive Summary

| Metric | Value |
| :--- | :--- |
| **Repositories Scanned** | 3 |
| **Migration Files Scanned** | 1121 |
| **Statements Parsed & Analyzed** | 7,745 |
| **Total Findings** | 989 |
| **Parse Failures** | 64 |
| **Total Duration** | 5.54 s |
| **Overall Throughput** | 1,398.6 statements/sec |

## Performance by Repository

| Repository | Files | Statements | Findings | Failures | Time (s) | Speed (stmts/s) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `supabase-auth` | 77 | 14 | 0 | 64 | 0.098 | 142.7 |
| `coder` | 751 | 7,097 | 691 | 0 | 5.031 | 1,410.7 |
| `crates-io` | 293 | 634 | 298 | 0 | 0.409 | 1,550.8 |

## Findings Breakdown by Rule

| Repository | MG001 | MG002 | MG005 | MG006 | MG009 | MG010 | MG011 | MG012 | Total |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `supabase-auth` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **0** |
| `coder` | 74 | 22 | 0 | 0 | 30 | 551 | 11 | 3 | **691** |
| `crates-io` | 29 | 5 | 3 | 1 | 2 | 237 | 13 | 8 | **298** |
| **Total** | **103** | **27** | **3** | **1** | **32** | **788** | **24** | **11** | **989** |

## Parse Failures

### `supabase-auth` (64 failures)

| File | Line | Error |
| :--- | :---: | :--- |
| `00_init_auth_schema.up.sql` | 3 | `syntax error at or near "{"` |
| `20210710035447_alter_users.up.sql` | 3 | `syntax error at or near "{"` |
| `20210722035447_adds_confirmed_at.up.sql` | 3 | `syntax error at or near "{"` |
| `20210730183235_add_email_change_confirmed.up.sql` | 3 | `syntax error at or near "{"` |
| `20210909172000_create_identities_table.up.sql` | 3 | `syntax error at or near "{"` |
| `20210927181326_add_refresh_token_parent.up.sql` | 3 | `syntax error at or near "{"` |
| `20211122151130_create_user_id_idx.up.sql` | 3 | `syntax error at or near "Namespace"` |
| `20211124214934_update_auth_functions.up.sql` | 3 | `syntax error at or near "{"` |
| `20211202183645_update_auth_uid.up.sql` | 3 | `syntax error at or near "{"` |
| `20220114185221_update_user_idx.up.sql` | 4 | `syntax error at or near "Namespace"` |
| `20220114185340_add_banned_until.up.sql` | 3 | `syntax error at or near "{"` |
| `20220224000811_update_auth_functions.up.sql` | 3 | `syntax error at or near "{"` |
| `20220323170000_add_user_reauthentication.up.sql` | 3 | `syntax error at or near "{"` |
| `20220429102000_add_unique_idx.up.sql` | 10 | `syntax error at or near "{"` |
| `20220531120530_add_auth_jwt_function.up.sql` | 3 | `syntax error at or near "{"` |
| `20220614074223_add_ip_address_to_audit_log.postgres.up.sql` | 2 | `syntax error at or near "{"` |
| `20220811173540_add_sessions_table.up.sql` | 2 | `syntax error at or near "{"` |
| `20221003041349_add_mfa_schema.up.sql` | 21 | `syntax error at or near "{"` |
| `20221003041400_add_aal_and_factor_id_to_sessions.up.sql` | 2 | `syntax error at or near "{"` |
| `20221011041400_add_mfa_indexes.up.sql` | 1 | `syntax error at or near "{"` |
| `20221020193600_add_sessions_user_id_index.up.sql` | 1 | `syntax error at or near "{"` |
| `20221021073300_add_refresh_tokens_session_id_revoked_index.up.sql` | 1 | `syntax error at or near "{"` |
| `20221021082433_add_saml.up.sql` | 3 | `syntax error at or near "{"` |
| `20221027105023_add_identities_user_id_idx.up.sql` | 1 | `syntax error at or near "{"` |
| `20221114143122_add_session_not_after_column.up.sql` | 1 | `syntax error at or near "{"` |
| `20221114143410_remove_parent_foreign_key_refresh_tokens.up.sql` | 1 | `syntax error at or near "{"` |
| `20221215195500_modify_users_email_unique_index.up.sql` | 6 | `syntax error at or near "{"` |
| `20221215195800_add_identities_email_column.up.sql` | 11 | `syntax error at or near "{"` |
| `20221215195900_remove_sso_sessions.up.sql` | 2 | `syntax error at or near "{"` |
| `20230116124412_add_deleted_at.up.sql` | 3 | `syntax error at or near "{"` |
| `20230322519590_add_flow_state_table.up.sql` | 7 | `syntax error at or near "{"` |
| `20230402418590_add_authentication_method_to_flow_state_table.up.sql` | 1 | `syntax error at or near "{"` |
| `20230411005111_remove_duplicate_idx.up.sql` | 1 | `syntax error at or near "{"` |
| `20230508135423_add_cleanup_indexes.up.sql` | 5 | `syntax error at or near "{"` |
| `20230523124323_add_mfa_challenge_cleanup_index.up.sql` | 5 | `syntax error at or near "{"` |
| `20230818113222_add_flow_state_to_relay_state.up.sql` | 1 | `syntax error at or near "{"` |
| `20230914180801_add_mfa_factors_user_id_idx.up.sql` | 1 | `syntax error at or near "{"` |
| `20231027141322_add_session_refresh_columns.up.sql` | 1 | `syntax error at or near "{"` |
| `20231114161723_add_sessions_tag.up.sql` | 1 | `syntax error at or near "{"` |
| `20231117164230_add_id_pkey_identities.up.sql` | 12 | `syntax error at or near "{"` |
| `20240729123726_add_mfa_phone_config.up.sql` | 8 | `syntax error at or near "{"` |
| `20240802193726_add_mfa_factors_column_last_challenged_at.up.sql` | 1 | `syntax error at or near "{"` |
| `20240806073726_drop_uniqueness_constraint_on_phone.up.sql` | 1 | `syntax error at or near "{"` |
| `20241009103726_add_web_authn.up.sql` | 1 | `syntax error at or near "{"` |
| `20250731150234_add_oauth_clients_table.up.sql` | 9 | `syntax error at or near "{"` |
| `20250804100000_add_oauth_authorizations_consents.up.sql` | 17 | `syntax error at or near "{"` |
| `20250901200500_add_oauth_client_type.up.sql` | 4 | `syntax error at or near "{"` |
| `20250903112500_remove_oauth_client_id_column.up.sql` | 5 | `syntax error at or near "{"` |
| `20250904133000_add_oauth_client_id_to_session.up.sql` | 1 | `syntax error at or near "{"` |
| `20250925093508_add_last_webauthn_challenge_data.up.sql` | 2 | `syntax error at or near "{"` |
| `20251007112900_add_session_refresh_token_columns.up.sql` | 2 | `syntax error at or near "{"` |
| `20251104100000_add_nonce_to_oauth_authorizations.up.sql` | 2 | `syntax error at or near "{"` |
| `20251111201300_add_scopes_to_sessions.up.sql` | 4 | `syntax error at or near "{"` |
| `20251201000000_add_oauth_client_states_table.up.sql` | 2 | `syntax error at or near "{"` |
| `20260115000000_add_flow_state_oauth_context.up.sql` | 3 | `syntax error at or near "{"` |
| `20260121000000_add_token_endpoint_auth_method.up.sql` | 5 | `syntax error at or near "{"` |
| `20260219120000_add_custom_oauth_providers.up.sql` | 5 | `syntax error at or near "{"` |
| `20260302000000_add_passkeys.up.sql` | 3 | `syntax error at or near "{"` |
| `20260625000000_add_custom_claims_allowlist.up.sql` | 5 | `syntax error at or near "{"` |
| `20260821000000_add_scim_users.up.sql` | 4 | `syntax error at or near "{"` |
| `20260821010000_add_scim_tokens.up.sql` | 4 | `syntax error at or near "{"` |
| `20260824000001_add_recovery_codes_tables.up.sql` | 2 | `syntax error at or near "{"` |
| `20260831180000_add_expires_at_to_one_time_tokens.up.sql` | 2 | `syntax error at or near "{"` |
| `20260911120000_add_link_token_hash_to_one_time_tokens.up.sql` | 3 | `syntax error at or near "{"` |

