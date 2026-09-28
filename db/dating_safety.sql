-- phpMyAdmin SQL Dump
-- version 5.2.1
-- https://www.phpmyadmin.net/
--
-- 主機： localhost
-- 產生時間： 2026 年 08 月 04 日 05:31
-- 伺服器版本： 10.4.28-MariaDB
-- PHP 版本： 8.2.4

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
START TRANSACTION;
SET time_zone = "+00:00";


/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;

--
-- 資料庫： `dating_safety`
--

-- --------------------------------------------------------

--
-- 資料表結構 `kb_configs`
--

CREATE TABLE `kb_configs` (
  `config_id` varchar(50) NOT NULL,
  `config_name` varchar(100) DEFAULT NULL,
  `thresholds` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`thresholds`)),
  `weights` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`weights`)),
  `decay_factor` float DEFAULT NULL,
  `context_window_size` int(11) DEFAULT NULL,
  `enabled` tinyint(1) DEFAULT 1,
  `applied_from` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- 傾印資料表的資料 `kb_configs`
--

INSERT INTO `kb_configs` (`config_id`, `config_name`, `thresholds`, `weights`, `decay_factor`, `context_window_size`, `enabled`, `applied_from`) VALUES
('threshold_v1', 'default_thresholds', '{\"B_OBSERVATION\": 0.10, \"B_WARNING\": 0.25, \"B_RESTRICTED\": 0.45, \"B_BLOCKED\": 0.65, \"SINGLE_DIM_OVERRIDE\": 0.85}', '{\"tiers\": [{\"min\": 0.0, \"max\": 0.1, \"beta\": 0.05, \"label\": \"Lv.1 極低信心：純規則主導\"}, {\"min\": 0.1, \"max\": 0.2, \"beta\": 0.15, \"label\": \"Lv.2 極低信心：規則強化\"}, {\"min\": 0.2, \"max\": 0.3, \"beta\": 0.25, \"label\": \"Lv.3 低信心：規則為主\"}, {\"min\": 0.3, \"max\": 0.4, \"beta\": 0.35, \"label\": \"Lv.4 低信心：規則稍重\"}, {\"min\": 0.4, \"max\": 0.5, \"beta\": 0.45, \"label\": \"Lv.5 中信心：平衡微調(Rule)\"}, {\"min\": 0.5, \"max\": 0.6, \"beta\": 0.55, \"label\": \"Lv.6 中信心：平衡微調(NLP)\"}, {\"min\": 0.6, \"max\": 0.7, \"beta\": 0.65, \"label\": \"Lv.7 高信心：語意稍重\"}, {\"min\": 0.7, \"max\": 0.8, \"beta\": 0.75, \"label\": \"Lv.8 高信心：語意為主\"}, {\"min\": 0.9, \"max\": 0.9, \"beta\": 0.85, \"label\": \"Lv.9 極高信心：語意強化\"}, {\"min\": 0.9, \"max\": 1.0, \"beta\": 0.95, \"label\": \"Lv.10 極高信心：純語意主導\"}], \"time_decay\": {\"unit\": \"hour\", \"tiers\": [{\"min_h\": 0, \"max_h\": 1, \"lambda\": 0.057, \"label\": \"0-1hr: Almost No Drop\"}, {\"min_h\": 1, \"max_h\": 24, \"lambda\": 0.028, \"label\": \"1-24hr: Normal Cooling\"}, {\"min_h\": 24, \"max_h\": 72, \"lambda\": 0.014, \"label\": \"1-3days: Getting Cold\"}, {\"min_h\": 72, \"max_h\": 168, \"lambda\": 0.009, \"label\": \"3-7days: Almost Cold\"}, {\"min_h\": 168, \"max_h\": 9999, \"lambda\": 0.693, \"label\": \"7days+: Reset/Zero\"}]}, \"decision_logic\": {\"W_MAX\": 0.60, \"W_SPREAD\": 0.30, \"W_TREND\": 0.10, \"NOISE_FLOOR\": 0.05, \"DECREASE_DAMPING\": 0.03, \"SPREAD_MODE\": \"count\"}}', 0.9, 5, 1, '2026-03-11 14:48:37'),
('threshold_v2_rule_heavy', 'rule_heavy_config', '{\"B_OBSERVATION\": 0.10, \"B_WARNING\": 0.20, \"B_RESTRICTED\": 0.45, \"B_BLOCKED\": 0.65, \"SINGLE_DIM_OVERRIDE\": 0.85}', '{\"tiers\": [{\"min\": 0.0, \"max\": 0.1, \"beta\": 0.05, \"label\": \"Lv.1\"}, {\"min\": 0.1, \"max\": 0.2, \"beta\": 0.10, \"label\": \"Lv.2\"}, {\"min\": 0.2, \"max\": 0.3, \"beta\": 0.20, \"label\": \"Lv.3\"}, {\"min\": 0.3, \"max\": 0.4, \"beta\": 0.25, \"label\": \"Lv.4\"}, {\"min\": 0.4, \"max\": 0.5, \"beta\": 0.30, \"label\": \"Lv.5\"}, {\"min\": 0.5, \"max\": 0.6, \"beta\": 0.40, \"label\": \"Lv.6\"}, {\"min\": 0.6, \"max\": 0.7, \"beta\": 0.45, \"label\": \"Lv.7\"}, {\"min\": 0.7, \"max\": 0.8, \"beta\": 0.50, \"label\": \"Lv.8\"}, {\"min\": 0.8, \"max\": 0.9, \"beta\": 0.55, \"label\": \"Lv.9\"}, {\"min\": 0.9, \"max\": 1.0, \"beta\": 0.60, \"label\": \"Lv.10 Capped\"}], \"time_decay\": {\"unit\": \"hour\", \"tiers\": [{\"min_h\": 0, \"max_h\": 1, \"lambda\": 0.057}, {\"min_h\": 1, \"max_h\": 24, \"lambda\": 0.028}, {\"min_h\": 24, \"max_h\": 72, \"lambda\": 0.014}, {\"min_h\": 72, \"max_h\": 168, \"lambda\": 0.009}, {\"min_h\": 168, \"max_h\": 9999, \"lambda\": 0.693}]}, \"decision_logic\": {\"W_MAX\": 0.60, \"W_SPREAD\": 0.30, \"W_TREND\": 0.10, \"NOISE_FLOOR\": 0.10, \"DECREASE_DAMPING\": 0.03, \"SPREAD_MODE\": \"effdim\"}}', 0.7, 5, 1, '2026-05-18 03:59:38');

-- --------------------------------------------------------

--
-- 資料表結構 `kb_features`
--

CREATE TABLE `kb_features` (
  `feature_id` int(11) NOT NULL,
  `feature_type` enum('REGEX','STATISTICAL','CONTEXT') NOT NULL DEFAULT 'REGEX',
  `category` varchar(50) NOT NULL,
  `feature_name` varchar(100) NOT NULL,
  `description` text DEFAULT NULL,
  `risk_delta` float DEFAULT 0,
  `regex_pattern` varchar(255) DEFAULT NULL,
  `logic_config` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`logic_config`)),
  `enabled` tinyint(1) DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- 傾印資料表的資料 `kb_features`
--

INSERT INTO `kb_features` (`feature_id`, `feature_type`, `category`, `feature_name`, `description`, `risk_delta`, `regex_pattern`, `logic_config`, `enabled`) VALUES
(1, 'REGEX', 'sexual_boundary', '身體部位詞彙', NULL, 0.02, NULL, NULL, 1),
(2, 'REGEX', 'sexual_boundary', '性暗示語句', NULL, 0.05, NULL, NULL, 1),
(3, 'REGEX', 'sexual_boundary', '要求裸照', NULL, 0.2, NULL, NULL, 1),
(4, 'REGEX', 'sexual_boundary', '邀請到私密空間', NULL, 0.2, NULL, NULL, 1),
(5, 'REGEX', 'sexual_boundary', '直接詢問性經驗', NULL, 0.15, NULL, NULL, 1),
(6, 'REGEX', 'coercion', '限時語句', NULL, 0.05, NULL, NULL, 1),
(7, 'REGEX', 'coercion', '二選一框架', NULL, 0.05, NULL, NULL, 1),
(8, 'REGEX', 'coercion', '威脅式語氣', NULL, 0.2, NULL, NULL, 1),
(9, 'REGEX', 'coercion', '條件交換', NULL, 0.2, NULL, NULL, 1),
(10, 'REGEX', 'coercion', '外流威脅', NULL, 0.3, NULL, NULL, 1),
(11, 'REGEX', 'manipulation', '虛假身份', NULL, 0.5, NULL, NULL, 1),
(12, 'REGEX', 'manipulation', '引導離開平台', NULL, 0.3, NULL, NULL, 1),
(13, 'REGEX', 'manipulation', '過度理想化', NULL, 0.05, NULL, NULL, 1),
(14, 'REGEX', 'manipulation', '命定論', NULL, 0.05, NULL, NULL, 1),
(15, 'REGEX', 'manipulation', '快速建立排他性', NULL, 0.1, NULL, NULL, 1),
(16, 'REGEX', 'manipulation', '典型詐騙特徵', NULL, 0.3, NULL, NULL, 1),
(17, 'REGEX', 'harassment', '重複傳訊', NULL, 0.05, NULL, NULL, 0),
(18, 'REGEX', 'harassment', '連續追問', NULL, 0.15, NULL, NULL, 1),
(19, 'REGEX', 'harassment', '未讀仍持續傳訊', NULL, 0.15, NULL, NULL, 0),
(20, 'REGEX', 'harassment', '辱罵詞彙', NULL, 0.2, NULL, NULL, 1),
(21, 'REGEX', 'harassment', '人身攻擊', NULL, 0.2, NULL, NULL, 1),
(22, 'REGEX', 'emotional_pressure', '強依附語句', NULL, 0.05, NULL, NULL, 1),
(23, 'REGEX', 'emotional_pressure', '自責語句', NULL, 0.05, NULL, NULL, 1),
(24, 'REGEX', 'emotional_pressure', '孤單暗示', NULL, 0.05, NULL, NULL, 1),
(25, 'REGEX', 'emotional_pressure', '悲情敘事', NULL, 0.05, NULL, NULL, 1),
(26, 'REGEX', 'emotional_pressure', '被拋棄焦慮暗示', NULL, 0.05, NULL, NULL, 1);

-- --------------------------------------------------------

--
-- 資料表結構 `kb_hard_blocks`
--

CREATE TABLE `kb_hard_blocks` (
  `id` int(11) NOT NULL,
  `keyword` varchar(100) NOT NULL COMMENT '絕對攔截的禁詞',
  `reason_label` varchar(50) DEFAULT 'general_safety' COMMENT '違規分類標籤',
  `trigger_mode` varchar(20) DEFAULT 'flag',
  `enabled` tinyint(1) DEFAULT 1 COMMENT '是否啟用',
  `created_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- 傾印資料表的資料 `kb_hard_blocks`
--

INSERT INTO `kb_hard_blocks` (`id`, `keyword`, `reason_label`, `trigger_mode`, `enabled`, `created_at`) VALUES
(1, '炸彈', 'violence', 'flag', 1, '2026-05-03 02:05:46'),
(2, '槍枝', 'violence', 'flag', 1, '2026-05-03 02:05:46'),
(3, '自殺', 'self_harm', 'flag', 1, '2026-05-03 02:05:46'),
(4, '毒品', 'illegal_drugs', 'flag', 1, '2026-05-03 02:05:46'),
(5, '殺人', 'violence', 'flag', 1, '2026-05-03 02:05:46'),
(6, '強姦', 'sexual_violence', 'flag', 1, '2026-05-03 02:05:46'),
(7, '裸照', 'sexual_content', 'flag', 1, '2026-05-03 02:05:46'),
(8, '殺死你', 'violence_threat', 'flag', 1, '2026-05-29 15:38:39'),
(9, '強姦你', 'sexual_violence_threat', 'flag', 1, '2026-05-29 15:38:39'),
(10, '傳裸照給我', 'sexual_demand', 'flag', 1, '2026-05-29 15:38:39'),
(11, '拍裸照給我', 'sexual_demand', 'flag', 1, '2026-05-29 15:38:39'),
(12, '不准拒絕', 'coercion_threat', 'flag', 1, '2026-09-27 20:02:00'),
(13, '傷害你的家人', 'violence_threat', 'flag', 1, '2026-09-27 20:02:00'),
(14, '去你家堵你', 'stalking_threat', 'flag', 1, '2026-09-27 20:02:00'),
(15, '宰了你', 'violence_threat', 'flag', 1, '2026-09-27 20:02:00'),
(16, '弄死你', 'violence_threat', 'flag', 1, '2026-09-27 20:02:00'),
(17, '性侵你', 'sexual_violence_threat', 'flag', 1, '2026-09-27 20:02:00'),
(18, '我知道你住哪', 'stalking_threat', 'flag', 1, '2026-09-27 20:02:00'),
(19, '把你宰了', 'violence_threat', 'flag', 1, '2026-09-27 20:02:00'),
(20, '把你弄死', 'violence_threat', 'flag', 1, '2026-09-27 20:02:00'),
(21, '把你殺了', 'violence_threat', 'flag', 1, '2026-09-27 20:02:00'),
(22, '把你砍死', 'violence_threat', 'flag', 1, '2026-09-27 20:02:00'),
(23, '殺了你', 'violence_threat', 'flag', 1, '2026-09-27 20:02:00'),
(24, '殺掉你', 'violence_threat', 'flag', 1, '2026-09-27 20:02:00'),
(25, '沒有拒絕的權利', 'coercion_threat', 'flag', 1, '2026-09-27 20:02:00'),
(26, '砍死你', 'violence_threat', 'flag', 1, '2026-09-27 20:02:00'),
(27, '私密照傳給', 'intimate_image_blackmail', 'flag', 1, '2026-09-27 20:02:00'),
(28, '私密照公開', 'intimate_image_blackmail', 'flag', 1, '2026-09-27 20:02:00'),
(29, '私密照外流', 'intimate_image_blackmail', 'flag', 1, '2026-09-27 20:02:00'),
(30, '裸照公開', 'intimate_image_blackmail', 'flag', 1, '2026-09-27 20:02:00'),
(31, '裸照外流', 'intimate_image_blackmail', 'flag', 1, '2026-09-27 20:02:00'),
(32, '跟蹤你', 'stalking_threat', 'flag', 1, '2026-09-27 20:02:00');

-- --------------------------------------------------------

--
-- 資料表結構 `kb_interventions`
--

CREATE TABLE `kb_interventions` (
  `template_id` varchar(50) NOT NULL,
  `risk_level` varchar(20) DEFAULT NULL,
  `primary_risk_type` varchar(50) DEFAULT NULL,
  `action_type` varchar(100) DEFAULT NULL,
  `message_template` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`message_template`)),
  `ui_behavior` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`ui_behavior`)),
  `created_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- 傾印資料表的資料 `kb_interventions`
--

INSERT INTO `kb_interventions` (`template_id`, `risk_level`, `primary_risk_type`, `action_type`, `message_template`, `ui_behavior`, `created_at`) VALUES
('block_receiver_notice', 'blocked', 'any', 'show_blocked_notice', '{\"body\": \"系統攔截了一則可能對你造成不適的訊息，你可以選擇繼續或結束這段對話\"}', '{\"show_options\": true}', '2026-03-31 14:14:58'),
('block_sender_final', 'blocked', 'any', 'block_message', '{\"title\": \"訊息未送出\", \"body\": \"這則訊息因為安全考量暫時無法送出，你的帳號功能已暫時限制\"}', '{\"cooldown\": 1800, \"require_ack\": true}', '2026-03-31 14:14:58'),
('obs_receiver_ambient', 'observation', 'any', 'show_ambient_icon', '{\"body\": \"你可以隨時封鎖或檢舉對方\"}', '{\"show_options\": false}', '2026-03-31 14:14:58'),
('receiver_info_warning', 'warning', 'any', 'show_safety_info_card', '{\"body\": \"系統偵測到對話中可能有不當模式，已對對方啟動提醒\"}', '{\"show_options\": false}', '2026-03-31 13:49:35'),
('restrict_receiver_options', 'restricted', 'any', 'show_safety_info_card', '{\"title\":\"已加強保護這段對話\",\"body\":\"系統偵測到對話中可能存在讓你不舒服的互動模式。你可以選擇繼續觀察、封鎖、檢舉，或暫時停止這段對話。\"}', '{\"show_options\":true,\"cooldown\":0,\"require_ack\":false}', '2026-05-25 03:26:19'),
('restrict_sender_general', 'restricted', 'any', 'show_modal_warning', '{\"title\": \"請暫停一下\", \"body\": \"系統偵測到對話中可能存在讓對方不舒適的模式，建議給對方一些空間\"}', '{\"cooldown\": 60, \"require_ack\": true}', '2026-03-31 13:49:35'),
('warn_sender_coercion', 'warning', 'coercion', 'show_reflection_banner', '{\"body\": \"好的對話建立在雙方都舒適的節奏上\"}', '{\"cooldown\": 0, \"require_ack\": false}', '2026-03-31 14:14:58'),
('warn_sender_emotional', 'warning', 'emotional_pressure', 'show_reflection_banner', '{\"body\": \"給彼此一些回覆的空間，有助於建立更好的連結\"}', '{\"cooldown\": 0, \"require_ack\": false}', '2026-03-31 13:49:35'),
('warn_sender_harassment', 'warning', 'harassment', 'show_reflection_banner', '{\"body\": \"頻繁的訊息有時會讓對方感到不舒服\"}', '{\"cooldown\": 0, \"require_ack\": false}', '2026-03-31 14:14:58'),
('warn_sender_manipulation', 'warning', 'manipulation', 'show_reflection_banner', '{\"body\": \"真誠的互動比快速建立好感更能讓關係長久\"}', '{\"cooldown\": 0, \"require_ack\": false}', '2026-03-31 13:49:35'),
('warn_sender_sexual', 'warning', 'sexual_boundary', 'show_reflection_banner', '{\"body\": \"尊重對方的邊界是建立信任的基礎\"}', '{\"cooldown\": 0, \"require_ack\": false}', '2026-03-31 13:49:35');

-- --------------------------------------------------------

--
-- 資料表結構 `kb_prompts`
--

CREATE TABLE `kb_prompts` (
  `prompt_id` varchar(50) NOT NULL,
  `prompt_type` varchar(50) DEFAULT NULL,
  `template` text DEFAULT NULL,
  `version` varchar(10) DEFAULT NULL,
  `model` varchar(50) DEFAULT NULL,
  `enabled` tinyint(1) DEFAULT 1,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- 傾印資料表的資料 `kb_prompts`
--

INSERT INTO `kb_prompts` (`prompt_id`, `prompt_type`, `template`, `version`, `model`, `enabled`, `created_at`) VALUES
('memory_summary_v1', 'rolling_summary', '你是社交安全分析師，負責產出對話片段的結構化摘要，作為下游風險偵測模組的情境依據。請以客觀、可解釋、低主觀色彩的方式進行分析。\n\n【背景資訊】\n- 雙方互動天數：{interaction_days} 天\n- 累積訊息總數：{total_messages} 則\n- 對話平衡度：{conversation_balance}（0.5 為完全平衡，越偏離越單向）\n- 對話發起者：{initiator_id}\n- 上一版摘要（如果有）：{previous_summary}\n\n【本次要分析的對話片段】\n{messages_chunk}\n\n【請完成以下四個任務】\n\n任務一：寫一段 100~200 字的自然語言摘要（將寫入 summary_content），描述：\n- 雙方目前的關係狀態與互動氛圍\n- 本期對話的主要走向\n- 是否有任何值得注意的互動模式（例如某方主導、話題快速推進、語氣突然轉變等）\n\n任務二：以 0~1 數值評估以下四個維度，並針對每個維度提供 1~2 句的判斷理由：\n\n1. self_disclosure_depth（自我揭露深度）\n   - 0.0~0.2：僅興趣、嗜好等表層資訊\n   - 0.2~0.4：個人經歷、輕度個人觀點\n   - 0.4~0.6：價值觀、家庭議題、深層想法\n   - 0.6~0.8：私密慾望、深層恐懼、強烈情感\n   - 0.8~1.0：性議題、極私密揭露\n\n2. emotional_intensity（情感強度）\n   - 0.0~0.2：平淡、客套\n   - 0.2~0.4：日常情緒交流\n   - 0.4~0.6：明顯情感投入\n   - 0.6~0.8：強烈情緒、依附傾向\n   - 0.8~1.0：激情、極端情感表達\n\n3. exclusivity_framing（排他性框架）\n   - 0.0：完全沒有排他性語言\n   - 0.3：偶爾出現「特別」「不一樣」之類的詞\n   - 0.6：明顯的「我們」框架建立\n   - 1.0：大量「命定」「唯一」「只有你」等排他語言\n\n4. physical_intimacy_reference（身體親密度提及）\n   - 0.0：完全沒有\n   - 0.3：提及單純見面\n   - 0.6：身體接觸的暗示或邀約\n   - 1.0：明確性議題討論\n\n任務三：從對話中抽出 3~5 個主要話題，以 JSON list 格式輸出（將寫入 main_topics）。話題應為具體名詞短語（例如「就學詢問」「週末邀約」），而非整句描述。\n\n任務四：描述語氣的演變方向（將寫入 tone_shift）。格式為「起始語氣 -> 結束語氣」，例如：\n- \"casual -> probing\"\n- \"friendly -> demanding\"\n- \"neutral -> affectionate\"\n若本期對話沒有明顯語氣轉變，則輸出 \"stable\"。\n\n【輸出格式（嚴格遵守 JSON，不得加入任何說明文字）】\n\n{{\n  \"summary_content\": \"對應任務一的 100~200 字摘要\",\n  \"scores\": {{\n    \"self_disclosure_depth\": 0.0,\n    \"emotional_intensity\": 0.0,\n    \"exclusivity_framing\": 0.0,\n    \"physical_intimacy_reference\": 0.0\n  }},\n  \"reasoning\": {{\n    \"self_disclosure\": \"對應 self_disclosure_depth 評分的判斷理由\",\n    \"emotional\": \"對應 emotional_intensity 評分的判斷理由\",\n    \"exclusivity\": \"對應 exclusivity_framing 評分的判斷理由\",\n    \"physical\": \"對應 physical_intimacy_reference 評分的判斷理由\"\n  }},\n  \"main_topics\": [\"話題1\", \"話題2\", \"話題3\"],\n  \"tone_shift\": \"起始語氣 -> 結束語氣，或 stable\"\n}}\n\n【輸出注意事項】\n- 嚴格遵守 JSON 格式，不要加入 markdown 標記\n- 數值欄位必須是 0.0~1.0 之間的浮點數\n- 若對話片段不足以判斷某維度，請給保守低估值並在 reasoning 中說明\n- 低溫度輸出（temperature=0.1）', '1.0', 'gemini-3.1-flash-lite', 1, '2026-05-18 02:07:00'),
('risk_analysis_v2', 'risk_detection', '你是社交安全分析專家。請根據當前訊息、已成功交付的對話歷史、後端計算的行為特徵、關係記憶與風險歷史，判斷此訊息是否涉及社交安全風險。\n\n請輸出可追溯的判斷依據與簡短推理摘要，不要展開冗長的內部思考過程。你的任務不是單看一句話，而是結合「這句話本身」、「雙方目前關係」、「互動節奏」與「是否存在單向施壓」做整體判斷。\n\n=== 第一部分：輸入資料 ===\n\n【當前訊息】\n發送者：{sender_id}\n內容：\"{current_message}\"\n時間：{timestamp}\n\n【已成功交付的對話歷史】（最近 5 句，僅包含雙方實際看見的訊息）\n{recent_messages}\n\n【後端計算的行為特徵】\n- reply_latency_seconds：{reply_latency_seconds}\n  說明：只有在「上一句由對方發送，而本句是我方回覆」時才有意義；若為 null，表示本句不是對上一位發言者的回覆。\n- idle_time_seconds：{idle_time_seconds}\n  說明：聊天室上一句訊息距離本句多久；它只代表聊天間隔，不等於對方故意不回。\n- unreplied_count：{unreplied_count}\n  說明：在對方尚未回覆前，發送者已連續傳了幾則訊息。\n- consecutive_char_count：{consecutive_char_count}\n  說明：在對方尚未回覆前，發送者目前這一段連續訊息累積了多少字。\n- message_ratio：{message_ratio}\n  說明：近期發送者訊息數佔比。\n- volume_ratio：{volume_ratio}\n  說明：近期發送者字數佔比。\n- avg_chars_per_message：{avg_chars_per_message}\n  說明：近期平均每則訊息字數。\n- frequency_score：{frequency}\n  說明：短時間連續發送強度，0 到 1，越高代表越密集。\n- message_burst_count：{message_burst_count}\n  說明：短時間連續發送次數。\n- conversation_turns：{conversation_turns}\n\n【關係記憶 L2】\n- 互動天數：{interaction_days}\n- 累積已交付訊息數：{total_messages}\n- 熟悉度 familiarity_score：{familiarity_score}\n- 對話平衡度 conversation_balance：{conversation_balance}\n  說明：0.5 代表雙方最平衡；越偏離 0.5 越單向。\n- 親密推進率 intimacy_progression_rate：{intimacy_progression_rate}\n\n【最新語意摘要 L1】\n{latest_summary}\n\n【既有風險歷史】\n{prior_risk_state}\n\n=== 風險特徵參考清單（輔助判斷，非硬性規則） ===\n以下為各風險維度的常見特徵與參考強度（高/中/低），供評分時對照。請以整體情境判斷為準：出現特徵不代表一定有風險，未列出的情形也可能有風險，切勿因命中關鍵詞就機械式給分。\n\n{feature_reference}\n\n=== 第二部分：判斷原則 ===\n\n請嚴格遵守以下原則：\n\n1. 區分「對方不回」與「雙方都沒講話」\n- `reply_latency_seconds` 與 `idle_time_seconds` 是不同概念。\n- `idle_time_seconds` 很長，只能代表聊天室閒置，不能單獨視為對方冷落或被騷擾。\n- 若 `reply_latency_seconds` 為 null，表示本句不是一則回覆，請不要把 `idle_time_seconds` 當成回覆延遲使用。\n\n2. 不要把「多則短句」直接等同於騷擾\n- 若 `message_burst_count` 高，但 `consecutive_char_count` 低、`avg_chars_per_message` 低、語意正常，可能只是使用者習慣把長句拆成幾則短訊息。\n- 只有在 `unreplied_count` 高、`volume_ratio` 明顯失衡、語意帶有催促/施壓/情緒勒索時，才應明顯提高 harassment 或 emotional_pressure。\n\n3. 行為風險要與語意風險交叉判斷\n- 高頻、連發、未被回覆，本身只是行為訊號。\n- 若同時出現命令、催促、羞辱、威脅、情緒勒索、性要求、越界邀約，風險才應顯著提高。\n- 若行為異常但語意正常，應保守評分並降低信心。\n\n4. 正確使用關係記憶\n- 低熟悉度 + 高親密推進率 + 快速排他/親密語言，可能代表 grooming 或不自然推進。\n- 高熟悉度 + 高互動平衡，可提高對曖昧語句的容忍度。\n- 但明確的性越界、威脅、操控、持續騷擾，不可因熟識而被洗掉。\n- `latest_summary` 用於理解關係走向，不可取代當前訊息本身的判斷。\n\n5. 時段是加乘因子，不是風險本身\n- 請參考【當前訊息】的時間（台北時間）。深夜 00:00–05:59 的越界或情緒施壓，對接收方壓力更大（對方多半正要休息、較難拒絕），同樣內容應比白天更嚴重。\n- 但深夜本身不構成風險。深夜的正常關心、道晚安、日常閒聊仍應判為無風險，切勿僅因時段就提高分數。\n\n6. 信心評估必須考量資訊完整度\n- 若最近對話不足 3 句，或 L1/L2 記憶不足，信心通常不應過高。\n- 若語境含糊、可能是玩笑、反諷、內部梗，應降低信心。\n- 若語意、行為、關係記憶三者互相支持，可提高信心。\n\n=== 第三部分：分析任務 ===\n\n步驟 1：行為模式識別\n請判斷：\n- 是否存在真正的未回覆持續施壓\n- 是否只是正常聊天室閒置後重新開始對話\n- 是否屬於多則短句但總字數不高的正常聊天習慣\n- 是否存在訊息量或字數量明顯失衡\n\n步驟 2：語義風險掃描\n五個風險維度以「作用機制」區分，非以語氣強度區分。請依各維度的判斷關鍵逐一檢視：\n\n- sexual_boundary（性界線）\n  機制：侵犯或試探對方的性界線與身體自主權。\n  判斷關鍵：是否將互動推向性領域或私密空間。\n  例：身體部位詞彙、性暗示語句、要求裸照、邀請到私密空間、直接詢問性經驗。\n\n- coercion（脅迫）\n  機制：透過威脅或設定負面後果，壓縮對方選擇空間。\n  判斷關鍵：是否存在「不照做會有後果」。\n  例：限時語句、二選一框架、威脅式語氣、條件交換、外流威脅。\n\n- manipulation（操控）\n  機制：透過資訊不對等或敘事框架，改變對方對情境的理解。\n  判斷關鍵：是否在改變「對方怎麼理解這段關係」。\n  例：虛假身份、引導離開平台、過度理想化、命定論、快速建立排他性、詐騙。\n\n- harassment（騷擾）\n  機制：在對方未回應或拒絕後，持續干擾其互動自由。\n  判斷關鍵：是否忽視對方界線並持續干擾。\n  例：重複傳訊、連續追問、未讀仍持續傳訊、辱罵詞彙、人身攻擊。\n\n- emotional_pressure（情緒壓力）\n  機制：透過情緒負擔影響對方傾向讓步。\n  判斷關鍵：是否透過情緒負擔而非威脅來促使讓步。\n  例：強依附語句、自責語句、孤單暗示、悲情敘事、被拋棄焦慮暗示。\n\n維度辨別提醒：\n- 靠威脅後果 → coercion；靠加重情緒負擔 → emotional_pressure。\n- 在改變對方對這段關係的理解 → manipulation；只是加重情緒負擔 → emotional_pressure。\n- 「罪惡感綁架」「情緒勒索」屬 emotional_pressure（情緒負擔機制），不是 manipulation（認知框架機制）。\n- 改變對方的認知 → manipulation；壓縮對方的選擇 → coercion。\n- 對方已表達拒絕或未回應而發送方仍持續 → harassment。此維度與其他維度並存，不可因已判定其他維度就略過 harassment 的給分。\n- 同一則訊息可同時成立多個維度（例如以外流要脅索取裸照＝coercion＋sexual_boundary），此時各維度均應給分。\n\n步驟 3：關係情境綜合判斷\n請綜合：\n- 當前訊息內容\n- 最近已交付對話\n- 行為特徵\n- L1 最新摘要\n- L2 關係指標\n- 既有風險歷史\n\n步驟 4：信心程度評估\n請針對每個風險維度在 0.0 到 1.0 之間評估信心：\n- 0.9-1.0：多重證據一致，語意與行為高度吻合\n- 0.7-0.8：證據充分，語境支持\n- 0.5-0.6：證據中等，仍有其他合理解釋\n- 0.3-0.4：證據偏弱，資訊不足\n- 0.0-0.2：高度不確定\n\n=== 第四部分：量化評分 ===\n\n風險分數標準：\n- 0.00-0.15：無明顯風險\n- 0.16-0.30：輕微跡象，需觀察\n- 0.31-0.50：中度風險，建議提示\n- 0.51-0.70：高風險，需要警告\n- 0.71-1.00：嚴重風險，考慮介入\n\n=== 第五部分：結構化輸出 ===\n\n請嚴格按照以下 JSON 格式輸出，不要加入 markdown 標記或額外說明文字：\n\n{{\n  \"analysis_steps\": {{\n    \"behavioral_signals\": \"<步驟1的精簡結論，50字內>\",\n    \"semantic_risks\": \"<步驟2的精簡結論，60字內>\",\n    \"contextual_judgment\": \"<步驟3的精簡結論，80字內>\"\n  }},\n  \"risk_scores\": {{\n    \"sexual_boundary\": {{\n      \"score\": <0-1浮點數>,\n      \"confidence\": <0-1浮點數>,\n      \"evidence\": \"<支持此判斷的證據，20字內>\"\n    }},\n    \"coercion\": {{\n      \"score\": <0-1浮點數>,\n      \"confidence\": <0-1浮點數>,\n      \"evidence\": \"<支持此判斷的證據，20字內>\"\n    }},\n    \"manipulation\": {{\n      \"score\": <0-1浮點數>,\n      \"confidence\": <0-1浮點數>,\n      \"evidence\": \"<支持此判斷的證據，20字內>\"\n    }},\n    \"harassment\": {{\n      \"score\": <0-1浮點數>,\n      \"confidence\": <0-1浮點數>,\n      \"evidence\": \"<支持此判斷的證據，20字內>\"\n    }},\n    \"emotional_pressure\": {{\n      \"score\": <0-1浮點數>,\n      \"confidence\": <0-1浮點數>,\n      \"evidence\": \"<支持此判斷的證據，20字內>\"\n    }}\n  }},\n  \"overall_assessment\": {{\n    \"average_confidence\": <所有維度信心的平均值>,\n    \"primary_concerns\": [\"<最主要的風險類型>\"],\n    \"uncertainty_factors\": [\"<影響判斷的不確定因素>\"],\n    \"recommended_action\": \"<observe/warn/intervene>\"\n  }},\n  \"detected_features\": [\"<列出你判定本則在情境中確實成立的特徵名稱，需對應上方風險特徵參考清單；若無則回空陣列 []>\"],\n  \"reasoning\": \"<可供稽核的完整判斷摘要，100字內>\"\n}}\n\n=== 輸出注意事項 ===\n- 嚴格輸出合法 JSON\n- 不要輸出 markdown\n- 所有 score 與 confidence 必須是 0.0 到 1.0 的浮點數\n- 若證據不足，請保守低估，不要為了迎合風險而過度推論\n- 若訊息屬於明確越界，即使關係熟悉，也不能因 L1/L2 記憶而過度降權', '2.0', 'gemini-3.1-flash-lite', 1, '2026-03-11 14:48:37');

-- --------------------------------------------------------

--
-- 資料表結構 `kb_rules`
--

CREATE TABLE `kb_rules` (
  `rule_id` varchar(50) NOT NULL,
  `rule_name` varchar(100) DEFAULT NULL,
  `description` text DEFAULT NULL,
  `conditions` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`conditions`)),
  `actions` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`actions`)),
  `priority` int(11) DEFAULT 1,
  `enabled` tinyint(1) DEFAULT 1,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  `updated_at` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- 傾印資料表的資料 `kb_rules`
--

INSERT INTO `kb_rules` (`rule_id`, `rule_name`, `description`, `conditions`, `actions`, `priority`, `enabled`, `created_at`, `updated_at`) VALUES
('rule_asym_message_volume', 'asymmetric_message_volume', '近期訊息數與字數都明顯偏向單方，提升單向施壓風險', '{\"message_ratio\":{\"operator\":\">=\",\"threshold\":0.75},\"volume_ratio\":{\"operator\":\">=\",\"threshold\":0.8},\"unreplied_count\":{\"operator\":\">=\",\"threshold\":3}}', '{\"harassment\":0.12,\"emotional_pressure\":0.06}', 70, 1, '2026-05-26 14:02:07', '2026-05-26 14:02:07'),
('rule_high_volume_push', 'high_volume_unanswered_push', '未回覆期間累積大量文字與高字數佔比，代表長篇催促或施壓', '{\"unreplied_count\":{\"operator\":\">=\",\"threshold\":3},\"consecutive_char_count\":{\"operator\":\">=\",\"threshold\":120},\"volume_ratio\":{\"operator\":\">=\",\"threshold\":0.75}}', '{\"harassment\":0.2,\"emotional_pressure\":0.1}', 90, 1, '2026-05-26 14:02:07', '2026-05-26 14:02:07'),
('rule_short_split_buffer', 'short_split_message_buffer', '短時間多則低字數訊息較可能是拆句習慣，對騷擾與情緒壓力做保守降權', '{\"message_burst_count\":{\"operator\":\">=\",\"threshold\":3},\"consecutive_char_count\":{\"operator\":\"<=\",\"threshold\":30},\"avg_chars_per_message\":{\"operator\":\"<=\",\"threshold\":8}}', '{\"harassment\":-0.12,\"emotional_pressure\":-0.08}', 60, 1, '2026-05-26 14:02:07', '2026-05-26 14:02:07'),
('rule_unreplied_pressure', 'unreplied_message_pressure', '對方尚未回覆前持續傳訊，形成基礎壓迫訊號', '{\"unreplied_count\":{\"operator\":\">=\",\"threshold\":4},\"frequency\":{\"operator\":\">=\",\"threshold\":0.7}}', '{\"harassment\":0.16,\"emotional_pressure\":0.08}', 80, 1, '2026-05-26 14:02:07', '2026-05-26 14:02:07');

-- --------------------------------------------------------

--
-- 資料表結構 `kb_scenario_rules`
--

CREATE TABLE `kb_scenario_rules` (
  `scenario_id` int(11) NOT NULL,
  `rule_name` varchar(100) NOT NULL,
  `description` text DEFAULT NULL,
  `condition_logic` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`condition_logic`)),
  `bonus_actions` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`bonus_actions`)),
  `enabled` tinyint(1) DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- 傾印資料表的資料 `kb_scenario_rules`
--

INSERT INTO `kb_scenario_rules` (`scenario_id`, `rule_name`, `description`, `condition_logic`, `bonus_actions`, `enabled`) VALUES
(1, 'midnight_aggressive_harassment', '深夜時段、性暗示且高頻連發。本規則的判別訊號是【持續性】，故加分側重 harassment；性暗示的強度由 midnight_sexual_push 負責', '{\"nlp_sexual_min\": 0.3, \"rule_freq_min\": 0.7, \"is_midnight\": true}', '{\"harassment\": 0.3, \"sexual_boundary\": 0.15}', 1),
(2, 'midnight_sexual_push', '深夜時段且性暗示達中高強度。本規則的判別訊號是【內容強度】，故加分側重 sexual_boundary；連發頻率由 midnight_aggressive_harassment 負責', '{\"nlp_sexual_min\": 0.4, \"is_midnight\": true}', '{\"sexual_boundary\": 0.3, \"harassment\": 0.15}', 1),
(3, 'obsessive_burst_pressure', '短時間內爆發式發送訊息且語氣帶有壓力', '{\"rule_burst_min\": 4, \"nlp_emotional_min\": 0.4}', '{\"emotional_pressure\": 0.3, \"coercion\": 0.1}', 1),
(4, 'pua_isolation_guilt', '利用語意進行情感勒索，試圖讓受害者產生愧疚感', '{\"nlp_manipulation_min\": 0.5, \"nlp_emotional_min\": 0.5}', '{\"manipulation\": 0.4}', 1),
(5, 'coercive_meeting', '在高頻發送的情境下，出現具備強制性的邀約語氣', '{\"rule_freq_min\": 0.7, \"nlp_coercion_min\": 0.5}', '{\"coercion\": 0.3, \"harassment\": 0.2}', 1),
(6, 'scam_logic_trap', '高度操縱性的語義結合不對等的訊息量', '{\"nlp_manipulation_min\": 0.6, \"rule_ratio_min\": 0.8}', '{\"manipulation\": 0.5}', 1),
(7, 'rebound_harassment', '對方尚未回覆前持續高頻傳訊，且累積文字與騷擾語意同步升高', '{\"rule_unreplied_min\":3,\"rule_freq_min\":0.75,\"rule_consecutive_char_min\":40,\"nlp_harassment_min\":0.4}', '{\"harassment\": 0.4, \"emotional_pressure\": 0.2}', 1),
(8, 'direct_body_request', '直接且高分值的性邊界侵犯', '{\"nlp_sexual_min\": 0.7}', '{\"sexual_boundary\": 0.4, \"harassment\": 0.2}', 1),
(9, 'gaslighting_pattern', '高度操控語義，試圖扭曲受害者認知', '{\"nlp_manipulation_min\": 0.7}', '{\"manipulation\": 0.4, \"emotional_pressure\": 0.3}', 1),
(10, 'midnight_emotional_blackmail', '深夜利用情緒低潮進行壓力灌輸', '{\"nlp_emotional_min\": 0.6, \"is_midnight\": true}', '{\"emotional_pressure\": 0.4}', 1),
(11, 'persistent_annoyance', '長期高頻且語意不佳', '{\"rule_freq_min\": 0.9, \"nlp_harassment_min\": 0.3}', '{\"harassment\": 0.3, \"emotional_pressure\": 0.2}', 1),
(12, 'abnormal_acceleration_grooming', '認識時間極短但親密度迅速飆升，符合掠奪者加速關係進程特徵', '{\"familiarity_max\": 0.50, \"intimacy_min\": 0.6, \"progression_rate_min\": 0.1}', '{\"manipulation\": 0.4, \"sexual_boundary\": 0.2}', 1),
(13, 'stable_familiar_relationship_t1', '熟人層級（熟悉度 0.63–0.83）且發言對稱，對邊界語句的容忍度略為提高；內容本身已達警戒者不適用', '{\"familiarity_min\": 0.63, \"familiarity_max\": 0.83, \"balance_range\": [0.4, 0.6], \"nlp_all_below\": 0.6}', '{\"manipulation\": -0.2, \"harassment\": -0.2, \"sexual_boundary\": -0.15, \"emotional_pressure\": -0.15}', 1),
(15, 'stable_familiar_relationship_t2', '熟識層級（熟悉度 0.83–0.956）且發言對稱，容忍度提高；內容本身已達警戒者不適用', '{\"familiarity_min\": 0.83, \"familiarity_max\": 0.956, \"balance_range\": [0.4, 0.6], \"nlp_all_below\": 0.6}', '{\"manipulation\": -0.3, \"harassment\": -0.3, \"sexual_boundary\": -0.22, \"emotional_pressure\": -0.22}', 1),
(16, 'stable_familiar_relationship_t3', '深交層級（熟悉度 0.956 以上）且發言對稱，容忍度明顯提高；內容本身已達警戒者不適用', '{\"familiarity_min\": 0.956, \"balance_range\": [0.4, 0.6], \"nlp_all_below\": 0.6}', '{\"manipulation\": -0.4, \"harassment\": -0.4, \"sexual_boundary\": -0.3, \"emotional_pressure\": -0.3}', 1),
(14, 'one_way_persistence_harassment', '對話高度失衡，且發訊方本身即為近期發言偏多的一方，代表其可能正在單向持續施壓', '{\"imbalance_min\": 0.6, \"intimacy_max\": 0.3, \"total_messages_min\": 10, \"rule_message_ratio_min\": 0.6}', '{\"harassment\": 0.3, \"emotional_pressure\": 0.2}', 1);

--
-- 已傾印資料表的索引
--

--
-- 資料表索引 `kb_configs`
--
ALTER TABLE `kb_configs`
  ADD PRIMARY KEY (`config_id`);

--
-- 資料表索引 `kb_features`
--
ALTER TABLE `kb_features`
  ADD PRIMARY KEY (`feature_id`);

--
-- 資料表索引 `kb_hard_blocks`
--
ALTER TABLE `kb_hard_blocks`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `uniq_keyword` (`keyword`),
  ADD KEY `idx_keyword_enabled` (`keyword`,`enabled`);

--
-- 資料表索引 `kb_interventions`
--
ALTER TABLE `kb_interventions`
  ADD PRIMARY KEY (`template_id`);

--
-- 資料表索引 `kb_prompts`
--
ALTER TABLE `kb_prompts`
  ADD PRIMARY KEY (`prompt_id`);

--
-- 資料表索引 `kb_rules`
--
ALTER TABLE `kb_rules`
  ADD PRIMARY KEY (`rule_id`);

--
-- 資料表索引 `kb_scenario_rules`
--
ALTER TABLE `kb_scenario_rules`
  ADD PRIMARY KEY (`scenario_id`);

--
-- 在傾印的資料表使用自動遞增(AUTO_INCREMENT)
--

--
-- 使用資料表自動遞增(AUTO_INCREMENT) `kb_features`
--
ALTER TABLE `kb_features`
  MODIFY `feature_id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=27;

--
-- 使用資料表自動遞增(AUTO_INCREMENT) `kb_hard_blocks`
--
ALTER TABLE `kb_hard_blocks`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=33;

--
-- 使用資料表自動遞增(AUTO_INCREMENT) `kb_scenario_rules`
--
ALTER TABLE `kb_scenario_rules`
  MODIFY `scenario_id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=15;
COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
