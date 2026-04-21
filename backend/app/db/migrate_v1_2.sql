-- Миграция v1.2: новые поля в keyword_stats и campaigns
-- Запускать ОДИН РАЗ вручную или через Alembic

-- keyword_stats: добавить новые поля если не существуют
ALTER TABLE keyword_stats
  ADD COLUMN IF NOT EXISTS weighted_impressions INTEGER,
  ADD COLUMN IF NOT EXISTS weighted_ctr NUMERIC(8, 4),
  ADD COLUMN IF NOT EXISTS bounce_rate NUMERIC(6, 2),
  ADD COLUMN IF NOT EXISTS sessions INTEGER;

-- campaigns: флаг ЕПК-обвала
ALTER TABLE campaigns
  ADD COLUMN IF NOT EXISTS epk_collapse_detected BOOLEAN DEFAULT FALSE;

-- hypotheses: нейтральный вердикт
-- ALTER TYPE hypothesisverdict ADD VALUE IF NOT EXISTS 'neutral';

COMMENT ON COLUMN keyword_stats.weighted_impressions IS 'WeightedImpressions — взвешенные показы с учётом позиции';
COMMENT ON COLUMN keyword_stats.weighted_ctr IS 'WeightedCtr — взвешенный CTR с учётом позиции, %';
COMMENT ON COLUMN keyword_stats.bounce_rate IS 'BounceRate из Директа — доля кликов-отказов, %';
COMMENT ON COLUMN keyword_stats.sessions IS 'Визиты из Метрики по utm_term (обогащение при сборе)';
COMMENT ON COLUMN campaigns.epk_collapse_detected IS 'Флаг: аналитик зафиксировал ЕПК-обвал ставок';
