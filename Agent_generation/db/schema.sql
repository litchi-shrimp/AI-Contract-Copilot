-- AI Contract Copilot 数据库结构（MySQL 5.7+/8.0，utf8mb4）
-- 使用：python -m db.init_db（自动建库建表，幂等）

-- ============================ 模板库 ============================
CREATE TABLE IF NOT EXISTS contract_category_tree (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  tree_json LONGTEXT NOT NULL COMMENT 'all_classify.json 原样存储（分类树）',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS contract_category (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  path VARCHAR(255) NOT NULL COMMENT '分类路径，如 技术合同.技术服务',
  name VARCHAR(128) NOT NULL,
  category_id VARCHAR(64) NOT NULL COMMENT '模板 ID 前缀，如 Tech_Service',
  level INT NOT NULL DEFAULT 1,
  UNIQUE KEY uk_path (path),
  KEY idx_category_id (category_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS contract_template (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  template_id VARCHAR(64) NOT NULL COMMENT '分类 ID，如 Real_Estate_Lease',
  template_key INT NOT NULL COMMENT '同分类下序号',
  template_name VARCHAR(500) NOT NULL,
  contract_type VARCHAR(255) NOT NULL DEFAULT '',
  party_type VARCHAR(64) NOT NULL DEFAULT '',
  scene VARCHAR(128) NOT NULL DEFAULT '',
  complexity VARCHAR(32) NOT NULL DEFAULT '',
  region VARCHAR(64) NOT NULL DEFAULT '',
  template_version VARCHAR(32) NOT NULL DEFAULT 'v1.0',
  source_file VARCHAR(1000) NOT NULL DEFAULT '',
  abstract TEXT,
  content_json LONGTEXT NOT NULL COMMENT 'standard_template_content',
  placeholder_json LONGTEXT,
  content_hash CHAR(40) NOT NULL DEFAULT '' COMMENT '内容指纹（幂等导入去重）',
  is_recommended TINYINT NOT NULL DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_tpl (template_id, template_key),
  KEY idx_hash (content_hash),
  KEY idx_meta (party_type, complexity, region)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================ 用户与会话 ============================
CREATE TABLE IF NOT EXISTS users (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(64) NOT NULL,
  password_hash VARCHAR(100) NOT NULL COMMENT 'bcrypt',
  display_name VARCHAR(64) NOT NULL DEFAULT '',
  status TINYINT NOT NULL DEFAULT 1 COMMENT '1 正常 0 禁用',
  failed_logins INT NOT NULL DEFAULT 0,
  locked_until DATETIME NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_login_at DATETIME NULL,
  UNIQUE KEY uk_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS refresh_tokens (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  token_hash CHAR(64) NOT NULL,
  expires_at DATETIME NOT NULL,
  revoked TINYINT NOT NULL DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_token (token_hash),
  KEY idx_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS conversations (
  id VARCHAR(32) PRIMARY KEY,
  user_id BIGINT NOT NULL,
  title VARCHAR(200) NOT NULL DEFAULT '新的合同',
  status VARCHAR(20) NOT NULL DEFAULT 'idle' COMMENT 'idle|running|waiting_input|paused|completed|failed|closed',
  current_step INT NOT NULL DEFAULT 1,
  state_json LONGTEXT COMMENT '流水线检查点（PipelineState）',
  state_version INT NOT NULL DEFAULT 0 COMMENT '乐观锁',
  run_epoch INT NOT NULL DEFAULT 0 COMMENT '每次启动运行 +1，旧运行写入被拒绝（防双写）',
  last_error VARCHAR(1000) NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted TINYINT NOT NULL DEFAULT 0,
  KEY idx_user_updated (user_id, deleted, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS messages (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  conversation_id VARCHAR(32) NOT NULL,
  role VARCHAR(16) NOT NULL COMMENT 'user|assistant|system',
  step INT NULL,
  content MEDIUMTEXT NOT NULL,
  meta_json TEXT,
  client_msg_id VARCHAR(64) NULL COMMENT '前端生成的幂等键，防止重复提交',
  created_at TIMESTAMP(3) DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_conv (conversation_id, id),
  UNIQUE KEY uk_client_msg (conversation_id, client_msg_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS conversation_events (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  conversation_id VARCHAR(32) NOT NULL,
  run_epoch INT NOT NULL DEFAULT 0,
  event_type VARCHAR(48) NOT NULL,
  step INT NULL,
  agent VARCHAR(64) NULL,
  data_json MEDIUMTEXT,
  created_at TIMESTAMP(3) DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_conv_id (conversation_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='聚合后的关键事件（可回放思考过程/工具调用）';

CREATE TABLE IF NOT EXISTS contract_versions (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  conversation_id VARCHAR(32) NOT NULL,
  step INT NOT NULL,
  kind VARCHAR(16) NOT NULL COMMENT 'outline|contract',
  content_json LONGTEXT NOT NULL,
  created_at TIMESTAMP(3) DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_conv (conversation_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS audit_log (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NULL,
  action VARCHAR(64) NOT NULL,
  target VARCHAR(64) NULL,
  ip VARCHAR(64) NULL,
  detail VARCHAR(1000) NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  KEY idx_user (user_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
