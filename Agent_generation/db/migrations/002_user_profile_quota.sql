-- 用户资料、免费额度、自有 API 配置（幂等）
CREATE TABLE IF NOT EXISTS user_quota (
  user_id BIGINT PRIMARY KEY,
  free_runs_used INT NOT NULL DEFAULT 0 COMMENT '已使用的免费完整合同生成次数（流程完成才计 1 次）',
  free_tokens_used BIGINT NOT NULL DEFAULT 0 COMMENT '平台免费 Key 已消耗 Token',
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS user_profile (
  user_id BIGINT PRIMARY KEY,
  avatar MEDIUMTEXT NULL COMMENT 'data URL（已在服务端校验类型与大小）',
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS user_models (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  provider VARCHAR(32) NOT NULL COMMENT '预置厂商 id 或 custom',
  display_name VARCHAR(64) NOT NULL,
  api_format VARCHAR(32) NOT NULL COMMENT 'openai_chat | openai_responses | anthropic | gemini',
  base_url VARCHAR(500) NOT NULL,
  model VARCHAR(200) NOT NULL,
  api_key_enc TEXT NOT NULL COMMENT 'Fernet 加密',
  api_key_hint VARCHAR(16) NOT NULL DEFAULT '' COMMENT '展示用：sk-…abcd',
  is_active TINYINT NOT NULL DEFAULT 0,
  last_test_ok TINYINT NULL,
  last_test_at DATETIME NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS usage_log (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  conversation_id VARCHAR(32) NOT NULL,
  run_epoch INT NOT NULL,
  billing VARCHAR(16) NOT NULL COMMENT 'free | own',
  model VARCHAR(200) NOT NULL DEFAULT '',
  tokens BIGINT NOT NULL DEFAULT 0,
  llm_calls INT NOT NULL DEFAULT 0,
  completed TINYINT NOT NULL DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  KEY idx_user (user_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
