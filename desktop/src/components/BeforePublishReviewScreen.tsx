/**
 * Phase 5 -- Before Publish Review Screen
 * Mandatory review gate: in-app video playback, QA scorecard,
 * verified claims, and one-click Approve & Publish / Regenerate Scene.
 */
import { useState } from 'react';
import type { JobInspect, ArtifactEntry } from '../api/types';
import CreativeQAPanel from './CreativeQAPanel';

function fileBase(p: string) {
  return p.replace(/\\/g, '/').split('/').pop() ?? p;
}

function VideoPreview({ artifact }: { artifact: ArtifactEntry | null }) {
  if (!artifact) {
    return (
      <div className='video-preview-placeholder'>
        <span className='muted'>No rendered video artifact found</span>
      </div>
    );
  }
  const path = String(artifact.artifact_path ?? artifact.file_path ?? artifact.path ?? '');
  return (
    <div className='video-preview-wrapper'>
      <div className='video-preview-meta muted small'>{fileBase(path)}</div>
      <div className='video-preview-placeholder'>
        <span className='muted'>Video preview: open file in system player</span>
        <a
          href={'file:///' + path.replace(/\\/g, '/')}
          target='_blank'
          rel='noopener noreferrer'
          className='ghost-btn'
          style={{ display: 'inline-block', marginTop: '8px' }}
        >
          Open in Player
        </a>
      </div>
    </div>
  );
}

function ClaimsVerificationPanel({ inspect }: { inspect: JobInspect }) {
  const manifest = (inspect.manifest ?? inspect.job ?? {}) as Record<string, unknown>;
  const claims = (() => {
    try {
      const raw = manifest.verified_claims_json ?? manifest.claims_json;
      if (typeof raw === 'string') return JSON.parse(raw) as { claim: string; verified: boolean }[];
    } catch { /* ignore */ }
    return null;
  })();

  if (!claims || claims.length === 0) {
    return (
      <div className='card'>
        <div className='card-header'><span>Verified Claims</span></div>
        <div className='card-body muted'>Claim verification data not available for this job.</div>
      </div>
    );
  }

  return (
    <div className='card'>
      <div className='card-header'>
        <span>Verified Claims</span>
        <span className='muted small'>{claims.filter(c => c.verified).length}/{claims.length} verified</span>
      </div>
      <ul className='plain-list' style={{ padding: '12px 16px' }}>
        {claims.map((c, i) => (
          <li key={i} style={{ padding: '4px 0', display: 'flex', gap: '8px', alignItems: 'flex-start' }}>
            <span style={{ color: c.verified ? '#2ec27e' : '#e5484d', flexShrink: 0 }}>{c.verified ? '\u2713' : '\u2717'}</span>
            <span>{c.claim}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

type RegenerateAction = { label: string; code: string };

const REGEN_ACTIONS: RegenerateAction[] = [
  { label: 'Regenerate all visuals', code: 'REGEN_ALL_VISUALS' },
  { label: 'Re-synthesize voice', code: 'REGEN_VOICE' },
  { label: 'Re-segment captions', code: 'REGEN_CAPTIONS' },
  { label: 'Re-render full video', code: 'REGEN_RENDER' },
];

export default function BeforePublishReviewScreen({
  inspect,
  onApprove,
  onRegenerate,
  isApproving,
  isRegenerating,
}: {
  inspect: JobInspect;
  onApprove: () => void;
  onRegenerate: (code: string) => void;
  isApproving: boolean;
  isRegenerating: boolean;
}) {
  const [regenConfirm, setRegenConfirm] = useState<string | null>(null);
  const artifacts = inspect.artifacts ?? [];
  const mediaArtifact = artifacts.find(a => a.artifact_type === 'media' || String(a.artifact_path ?? a.file_path ?? '').endsWith('.mp4')) ?? null;
  const qaReports = inspect.qa_reports ?? [];
  const latestQa = qaReports[qaReports.length - 1] ?? null;

  const handleRegen = (code: string) => {
    if (regenConfirm !== code) { setRegenConfirm(code); setTimeout(() => setRegenConfirm(null), 3500); return; }
    setRegenConfirm(null);
    onRegenerate(code);
  };

  return (
    <div className='before-publish-screen'>
      <div className='toolbar'>
        <div>
          <h2 className='page-title' id='before-publish-title'>Before Publish Review</h2>
          <p className='muted small'>
            Review the rendered video, QA scorecard, and verified claims before publishing.
            This is a mandatory gate — approvals cannot be undone.
          </p>
        </div>
        <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
          <button
            id='btn-approve-publish'
            className='primary-btn'
            onClick={onApprove}
            disabled={isApproving || isRegenerating}
          >
            {isApproving ? 'Approving\u2026' : '\u2713 Approve & Publish'}
          </button>
        </div>
      </div>

      <div className='before-publish-grid'>
        <div className='before-publish-left'>
          <VideoPreview artifact={mediaArtifact} />
          <ClaimsVerificationPanel inspect={inspect} />
        </div>
        <div className='before-publish-right'>
          {latestQa ? (
            <CreativeQAPanel report={latestQa} />
          ) : (
            <div className='card'>
              <div className='card-header'><span>Creative QA Scorecard</span></div>
              <div className='card-body muted'>QA report not yet available.</div>
            </div>
          )}
          <div className='card' style={{ marginTop: '14px' }}>
            <div className='card-header'><span>Targeted Regeneration</span></div>
            <div className='card-body'>
              <p className='muted small' style={{ marginBottom: '10px' }}>
                Surgically regenerate only the defective component without re-running the full pipeline.
              </p>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                {REGEN_ACTIONS.map(a => (
                  <button
                    key={a.code}
                    id={'btn-regen-' + a.code.toLowerCase()}
                    className='ghost-btn'
                    disabled={isRegenerating || isApproving}
                    onClick={() => handleRegen(a.code)}
                    style={{ borderColor: regenConfirm === a.code ? '#e6a23c' : undefined, color: regenConfirm === a.code ? '#e6a23c' : undefined }}
                  >
                    {regenConfirm === a.code ? 'Confirm?' : a.label}
                  </button>
                ))}
              </div>
              {isRegenerating && <p className='muted small' style={{ marginTop: '8px' }}>Regeneration in progress\u2026</p>}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
