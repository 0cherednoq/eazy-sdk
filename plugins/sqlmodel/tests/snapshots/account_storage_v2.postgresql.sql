CREATE TABLE accounts (
	id UUID NOT NULL,
	provider VARCHAR NOT NULL,
	identifier VARCHAR NOT NULL,
	remote_id VARCHAR,
	status VARCHAR NOT NULL,
	revision INTEGER NOT NULL,
	credentials JSON NOT NULL,
	profile JSON NOT NULL,
	meta JSON NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_accounts_provider_identifier UNIQUE (provider, identifier),
	CONSTRAINT ck_accounts_revision_nonnegative CHECK (revision >= 0)
);

CREATE INDEX ix_accounts_provider_status_created ON accounts (provider, status, created_at);

CREATE UNIQUE INDEX uq_accounts_provider_remote_id ON accounts (provider, remote_id) WHERE remote_id IS NOT NULL;

CREATE TABLE sessions (
	id UUID NOT NULL,
	account_id UUID NOT NULL,
	key VARCHAR NOT NULL,
	revision INTEGER NOT NULL,
	kind VARCHAR NOT NULL,
	payload JSON NOT NULL,
	expires_at TIMESTAMP WITHOUT TIME ZONE,
	is_active BOOLEAN NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_sessions_account_key UNIQUE (account_id, key),
	CONSTRAINT ck_sessions_revision_nonnegative CHECK (revision >= 0),
	FOREIGN KEY(account_id) REFERENCES accounts (id) ON DELETE CASCADE
);

CREATE INDEX ix_sessions_account_active_expiry ON sessions (account_id, is_active, expires_at);

CREATE TABLE verifications (
	id UUID NOT NULL,
	account_id UUID NOT NULL,
	via_account_id UUID,
	challenge_id VARCHAR NOT NULL,
	kind VARCHAR NOT NULL,
	status VARCHAR NOT NULL,
	target VARCHAR,
	expires_at TIMESTAMP WITHOUT TIME ZONE,
	attempts_remaining INTEGER,
	replaces_id UUID,
	meta JSON NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	verified_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_verifications_challenge UNIQUE (account_id, challenge_id),
	CONSTRAINT ck_verifications_attempts_nonnegative CHECK (attempts_remaining IS NULL OR attempts_remaining >= 0),
	FOREIGN KEY(account_id) REFERENCES accounts (id) ON DELETE CASCADE,
	FOREIGN KEY(via_account_id) REFERENCES accounts (id) ON DELETE SET NULL,
	FOREIGN KEY(replaces_id) REFERENCES verifications (id) ON DELETE SET NULL
);

CREATE INDEX ix_verifications_account_status_created ON verifications (account_id, status, created_at);

CREATE INDEX ix_verifications_via_created ON verifications (via_account_id, created_at);

CREATE TABLE account_links (
	id UUID NOT NULL,
	owner_account_id UUID NOT NULL,
	resource_account_id UUID NOT NULL,
	relation VARCHAR NOT NULL,
	status VARCHAR NOT NULL,
	exclusive_scope VARCHAR,
	meta JSON NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	released_at TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT ck_account_links_not_self CHECK (owner_account_id != resource_account_id),
	CONSTRAINT uq_account_links_relation UNIQUE (owner_account_id, resource_account_id, relation),
	CONSTRAINT uq_account_links_resource_scope UNIQUE (resource_account_id, exclusive_scope),
	FOREIGN KEY(owner_account_id) REFERENCES accounts (id) ON DELETE RESTRICT,
	FOREIGN KEY(resource_account_id) REFERENCES accounts (id) ON DELETE RESTRICT
);

CREATE TABLE account_events (
	id UUID NOT NULL,
	account_id UUID NOT NULL,
	type VARCHAR NOT NULL,
	occurred_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	correlation_id VARCHAR,
	session_id UUID,
	verification_id UUID,
	link_id UUID,
	data JSON NOT NULL,
	meta JSON NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(account_id) REFERENCES accounts (id) ON DELETE CASCADE,
	FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE SET NULL,
	FOREIGN KEY(verification_id) REFERENCES verifications (id) ON DELETE SET NULL,
	FOREIGN KEY(link_id) REFERENCES account_links (id) ON DELETE SET NULL
);

CREATE INDEX ix_account_events_account_occurred ON account_events (account_id, occurred_at);

CREATE INDEX ix_account_events_account_type_occurred ON account_events (account_id, type, occurred_at);

CREATE INDEX ix_account_events_correlation ON account_events (correlation_id);
