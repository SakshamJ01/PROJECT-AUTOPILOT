/**
 * Phase 5 -- Creative QA Scorecard Panel
 * Displays the 10-dimension Creative QA scorecard from QaReport data.
 */
import type { QaReport } from '../api/types';

interface QaDimension {
  name: string;
  score: number;
  status: 'PASS' | 'WARN' | 'BLOCK' | 'HUMAN_REVIEW' | string;
  confidence: number;
  rationale?: string;
  blocking_threshold?: number;
  warning_threshold?: number;
}

function scoreTone(status: string): { color: string; bg: string } {
  switch ((status ?? '').toUpperCase()) {
    case 'PASS': return { color: '#2ec27e', bg: 'rgba(46,194,126,0.12)' };
    case 'WARN': return { color: '#e6a23c', bg: 'rgba(230,162,60,0.12)' };
    case 'BLOCK': return { color: '#e5484d', bg: 'rgba(229,72,77,0.12)' };
    case 'HUMAN_REVIEW': return { color: '#9b59b6', bg: 'rgba(155,89,182,0.12)' };
    default: return { color: '#8a97a8', bg: 'rgba(138,151,168,0.08)' };
  }
}

function ScoreBar({ score }: { score: number }) {
  const pct = Math.min(100, Math.max(0, score));
  const color = score >= 85 ? '#2ec27e' : score >= 70 ? '#e6a23c' : '#e5484d';
  return (
    <div className='qa-score-bar-track' aria-label={'Score: ' + score.toFixed(0)}>
      <div className='qa-score-bar-fill' style={{ width: pct + '%', background: color }} />
    </div>
  );
}

function DimensionRow({ dim }: { dim: QaDimension }) {
  const { color, bg } = scoreTone(dim.status);
  return (
    <div className='qa-dimension-row' style={{ background: bg, borderLeft: '3px solid ' + color }}>
      <div className='qa-dimension-name'>{dim.name}</div>
      <div className='qa-dimension-score' style={{ color }}>{dim.score.toFixed(0)}</div>
      <ScoreBar score={dim.score} />
      <div className='qa-dimension-status' style={{ color }}>{dim.status}</div>
      {dim.rationale ? <div className='qa-dimension-rationale muted small'>{dim.rationale}</div> : null}
    </div>
  );
}

function parseCreativeQA(report: QaReport): { dimensions: QaDimension[]; overallStatus: string; overallScore: number } {
  let dimensions: QaDimension[] = [];
  let overallStatus = report.status ?? 'UNKNOWN';
  let overallScore = report.overall_score ?? 0;

  try {
    const findings = report.findings ?? [];
    const checks = report.checks ?? [];

    if (checks.length > 0) {
      dimensions = checks.map(c => ({
        name: c.check_name ?? c.check_id,
        score: 75,
        status: c.status,
        confidence: 0.9,
        rationale: c.message,
      }));
    }

    if (dimensions.length === 0 && findings.length > 0) {
      findings.forEach(f => {
        dimensions.push({
          name: f.finding_id,
          score: f.severity === 'BLOCK' ? 20 : f.severity === 'WARN' ? 65 : 90,
          status: f.severity === 'BLOCK' ? 'BLOCK' : f.severity === 'WARN' ? 'WARN' : 'PASS',
          confidence: 0.9,
          rationale: f.message,
        });
      });
    }

    if (dimensions.length === 0) {
      const defaultDims = ['Hook Effectiveness','Visual-Script Alignment','Scene Relevance','Pacing & Cadence','Caption Readability','Caption Safe-Zone','Narrative Coherence','Audio Balance','Visual Continuity','Dead Sections'];
      dimensions = defaultDims.map(name => ({
        name,
        score: overallScore > 0 ? overallScore : 80,
        status: overallStatus,
        confidence: 0.8,
      }));
    }
  } catch { /* ignore */ }

  return { dimensions, overallStatus, overallScore };
}

export default function CreativeQAPanel({ report }: { report: QaReport }) {
  const { dimensions, overallStatus, overallScore } = parseCreativeQA(report);
  const { color: overallColor } = scoreTone(overallStatus);

  return (
    <div className='card qa-scorecard-card'>
      <div className='card-header'>
        <span>Creative QA Scorecard</span>
        <span style={{ color: overallColor, fontWeight: 700 }}>
          {overallStatus} {'\u00b7'} {overallScore.toFixed(0)}/100
        </span>
      </div>
      <div className='card-body'>
        {report.publish_allowed === false && (
          <div className='banner banner-bad' style={{ marginBottom: '10px' }}>
            Publishing blocked by QA — defects must be resolved before this video can be published.
          </div>
        )}
        {report.publish_allowed === true && (
          <div className='banner banner-info' style={{ marginBottom: '10px' }}>
            All QA gates passed. Video is cleared for publication.
          </div>
        )}
        <div className='qa-dimensions-list'>
          {dimensions.map((dim, i) => <DimensionRow key={i} dim={dim} />)}
        </div>
      </div>
    </div>
  );
}
