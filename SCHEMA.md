# SCHEMA - 31 tables


## admin_settings

| Column | Type | Nullable | Default |
|---|---|---|---|
| key | character varying(100) | NO |  |
| value | text | NO |  |
| updated_at | timestamp without time zone | YES | now() |

PK: key

## bulletin_notes

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| user_id | uuid | NO |  |
| bulletin_id | uuid | YES |  |
| matiere | character varying(120) | NO |  |
| note | numeric | NO |  |
| coefficient | numeric | YES |  |
| created_at | timestamp with time zone | YES | now() |

PK: id

FK: user_id -> users.id

FK: bulletin_id -> bulletins.id

## bulletins

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO |  |
| user_id | uuid | NO |  |
| original_name | text | NO |  |
| stored_name | text | NO |  |
| uploaded_at | timestamp with time zone | YES | now() |

PK: id

FK: user_id -> users.id

## concours_alerts_sent

| Column | Type | Nullable | Default |
|---|---|---|---|
| user_id | uuid | NO |  |
| concours_id | uuid | NO |  |
| sent_at | timestamp without time zone | YES | now() |

PK: user_id, concours_id

## concours_calendar

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| name | character varying(200) | NO |  |
| school | character varying(150) | NO |  |
| category | character varying(100) | NO |  |
| registration_start | date | YES |  |
| registration_end | date | YES |  |
| exam_date | date | YES |  |
| results_date | date | YES |  |
| description | text | YES |  |
| official_link | text | YES |  |
| is_active | boolean | YES | true |
| created_at | timestamp without time zone | YES | now() |

PK: id

## concours_subscriptions

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| user_id | uuid | YES |  |
| concours_id | uuid | YES |  |
| created_at | timestamp without time zone | YES | now() |

PK: id

FK: user_id -> users.id

FK: concours_id -> concours_calendar.id

## email_verification_tokens

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| user_id | uuid | NO |  |
| email | character varying(255) | YES |  |
| code | character varying(10) | YES |  |
| token_hash | text | YES |  |
| expires_at | timestamp without time zone | NO |  |
| created_at | timestamp without time zone | NO | now() |
| is_used | boolean | YES | false |

PK: id

FK: user_id -> users.id

## etablissements

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| nom | character varying(300) | NO |  |
| sigle | character varying(50) | YES |  |
| categorie | character varying(150) | YES |  |
| secteur | character varying(100) | YES |  |
| ville | character varying(150) | YES |  |
| site_web | text | YES |  |
| telephone | character varying(50) | YES |  |
| adresse | text | YES |  |
| frais_annuels | numeric | YES |  |
| note_bac_min | numeric | YES |  |
| filieres | ARRAY | YES |  |
| debouches | ARRAY | YES |  |
| concours | ARRAY | YES |  |
| duree_etudes | character varying(50) | YES |  |
| groupe | character varying(100) | YES |  |
| created_at | timestamp without time zone | YES | now() |

PK: id

## forum_likes

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| user_id | uuid | YES |  |
| post_id | uuid | YES |  |
| reply_id | uuid | YES |  |
| created_at | timestamp without time zone | YES | now() |

PK: id

FK: user_id -> users.id

## forum_posts

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| user_id | uuid | YES |  |
| title | character varying(300) | NO |  |
| content | text | NO |  |
| category | character varying(100) | NO |  |
| school | character varying(150) | YES |  |
| likes | integer | YES | 0 |
| views | integer | YES | 0 |
| created_at | timestamp without time zone | YES | now() |
| updated_at | timestamp without time zone | YES | now() |
| is_pinned | boolean | YES | false |
| is_locked | boolean | YES | false |

PK: id

FK: user_id -> users.id

## forum_replies

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| post_id | uuid | YES |  |
| user_id | uuid | YES |  |
| content | text | NO |  |
| likes | integer | YES | 0 |
| created_at | timestamp without time zone | YES | now() |
| parent_reply_id | uuid | YES |  |

PK: id

FK: post_id -> forum_posts.id

FK: user_id -> users.id

FK: parent_reply_id -> forum_replies.id

## mentor_requests

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| requester_id | uuid | YES |  |
| mentor_id | uuid | YES |  |
| message | text | YES |  |
| created_at | timestamp without time zone | YES | now() |

PK: id

FK: requester_id -> users.id

FK: mentor_id -> mentors.id

## mentors

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| user_id | uuid | YES |  |
| school | character varying(200) | NO |  |
| filiere | character varying(200) | NO |  |
| bio | text | YES |  |
| available | boolean | YES | true |
| created_at | timestamp without time zone | YES | now() |
| updated_at | timestamp without time zone | YES | now() |

PK: id

FK: user_id -> users.id

## notifications

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| user_id | uuid | YES |  |
| title | character varying(200) | NO |  |
| message | text | NO |  |
| type | character varying(50) | YES | 'info'::character varying |
| link | text | YES |  |
| is_read | boolean | YES | false |
| created_at | timestamp with time zone | YES | now() |

PK: id

FK: user_id -> users.id

## oauth_accounts

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| user_id | uuid | NO |  |
| provider | character varying(50) | NO |  |
| provider_user_id | character varying(255) | NO |  |
| created_at | timestamp without time zone | NO | now() |

PK: id

FK: user_id -> users.id

## orientation_results

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO |  |
| user_id | uuid | NO |  |
| ecole | character varying(255) | YES |  |
| filiere | character varying(255) | YES |  |
| confidence | double precision | YES |  |
| alternatives | jsonb | YES |  |
| raw_answers | jsonb | YES |  |
| created_at | timestamp with time zone | YES | now() |

PK: id

FK: user_id -> users.id

## password_reset_tokens

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| user_id | uuid | NO |  |
| token | character varying(255) | YES |  |
| expires_at | timestamp without time zone | NO |  |
| created_at | timestamp without time zone | NO | now() |
| token_hash | text | YES |  |
| is_used | boolean | YES | false |

PK: id

FK: user_id -> users.id

## phone_otp_codes

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| phone_number | character varying(30) | NO |  |
| otp_code | character varying(10) | NO |  |
| expires_at | timestamp without time zone | NO |  |
| created_at | timestamp without time zone | NO | now() |

PK: id

## post_reactions

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| post_id | uuid | YES |  |
| reply_id | uuid | YES |  |
| user_id | uuid | YES |  |
| reaction_type | character varying(20) | NO |  |
| created_at | timestamp without time zone | YES | now() |

PK: id

FK: post_id -> forum_posts.id

FK: reply_id -> forum_replies.id

FK: user_id -> users.id

## private_room_members

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| room_id | uuid | NO |  |
| user_id | uuid | NO |  |
| is_host | boolean | YES | false |
| joined_at | timestamp without time zone | YES | now() |
| left_at | timestamp without time zone | YES |  |
| total_minutes | integer | YES | 0 |

PK: 

## private_rooms

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| host_id | uuid | NO |  |
| name | character varying(255) | NO |  |
| code | character varying(10) | NO |  |
| total_minutes | integer | YES | 25 |
| max_participants | integer | YES | 4 |
| is_active | boolean | YES | true |
| created_at | timestamp without time zone | YES | now() |
| updated_at | timestamp without time zone | YES | now() |
| subject | character varying(100) | YES |  |
| description | text | YES |  |

PK: 

## push_subscriptions

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| user_id | uuid | YES |  |
| endpoint | text | NO |  |
| p256dh | text | YES |  |
| auth_key | text | YES |  |
| created_at | timestamp with time zone | YES | now() |

PK: id

FK: user_id -> users.id

## schools_chat_history

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| user_id | uuid | YES |  |
| question | text | NO |  |
| answer | text | NO |  |
| created_at | timestamp without time zone | YES | now() |

PK: id

FK: user_id -> users.id

## shared_documents

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| user_id | uuid | YES |  |
| title | character varying(200) | NO |  |
| school | character varying(150) | YES |  |
| type | character varying(50) | NO | 'autre'::character varying |
| file_url | text | NO |  |
| is_approved | boolean | YES | false |
| created_at | timestamp without time zone | YES | now() |

PK: id

FK: user_id -> users.id

## solo_study_sessions

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO |  |
| user_id | uuid | NO |  |
| started_at | timestamp with time zone | NO | now() |
| ended_at | timestamp with time zone | YES |  |
| duration_minutes | numeric | YES |  |

PK: id

## student_profiles

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| user_id | uuid | NO |  |
| nom | character varying(120) | YES |  |
| prenom | character varying(120) | YES |  |
| niveau | character varying(120) | YES |  |
| filiere | character varying(120) | YES |  |
| profile_photo_url | text | YES |  |
| created_at | timestamp without time zone | NO | now() |
| updated_at | timestamp without time zone | NO | now() |
| telephone | character varying(20) | YES |  |
| date_naissance | date | YES |  |
| ville | character varying(100) | YES |  |
| filiere_actuelle | character varying(100) | YES |  |
| etablissement | character varying(150) | YES |  |
| annee_scolaire | character varying(20) | YES |  |
| moyenne_generale | numeric | YES |  |
| type_ecole | character varying(50) | YES |  |
| nom_ecole | character varying(200) | YES |  |
| avatar_url | text | YES |  |
| type_bac | character varying(100) | YES |  |
| note_bac | numeric | YES |  |
| show_in_leaderboard | boolean | YES | false |

PK: id

FK: user_id -> users.id

## study_room_participants

| Column | Type | Nullable | Default |
|---|---|---|---|
| room_id | uuid | NO |  |
| student_id | uuid | NO |  |
| joined_at | timestamp without time zone | YES | now() |
| left_at | timestamp without time zone | YES |  |
| total_minutes | integer | YES | 0 |
| is_present | boolean | YES | true |

PK: 

## study_rooms

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| host_id | uuid | YES |  |
| nom | character varying(150) | NO |  |
| description | text | YES |  |
| sujet | character varying(100) | YES |  |
| code_acces | character varying(10) | YES |  |
| max_participants | integer | YES | 10 |
| is_public | boolean | YES | true |
| is_active | boolean | YES | true |
| created_at | timestamp with time zone | YES | now() |
| closed_at | timestamp with time zone | YES |  |
| category | character varying(20) | YES | 'general'::character varying |
| tag | character varying(100) | YES |  |

PK: id

FK: host_id -> users.id

## temoignages

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| user_id | uuid | YES |  |
| school | character varying(150) | NO |  |
| filiere | character varying(150) | YES |  |
| annee_entree | character varying(10) | YES |  |
| content | text | NO |  |
| rating | integer | YES | 5 |
| is_approved | boolean | YES | false |
| created_at | timestamp without time zone | YES | now() |

PK: id

FK: user_id -> users.id

## user_sessions

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| user_id | uuid | NO |  |
| refresh_token | text | YES |  |
| expires_at | timestamp without time zone | NO |  |
| created_at | timestamp without time zone | NO | now() |
| refresh_token_hash | text | YES |  |
| device_info | text | YES |  |
| ip_address | character varying(50) | YES |  |
| user_agent | text | YES |  |
| is_revoked | boolean | YES | false |

PK: id

FK: user_id -> users.id

## users

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | uuid | NO | gen_random_uuid() |
| email | character varying(255) | YES |  |
| password_hash | text | YES |  |
| phone_number | character varying(30) | YES |  |
| role | character varying(30) | NO | 'student'::character varying |
| is_email_verified | boolean | NO | false |
| is_phone_verified | boolean | NO | false |
| auth_provider | character varying(30) | NO | 'email'::character varying |
| status | character varying(20) | NO | 'active'::character varying |
| last_login_at | timestamp without time zone | YES |  |
| created_at | timestamp without time zone | NO | now() |
| updated_at | timestamp without time zone | NO | now() |
| google_id | character varying(255) | YES |  |
| avatar_url | character varying(500) | YES |  |

PK: id