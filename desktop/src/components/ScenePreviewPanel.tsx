/**
 * Phase 5 -- Scene-Level Preview Renderer Panel
 * Lightweight renderer for previewing a single edited scene
 * in <3 seconds without full-video re-rendering.
 * Uses the backend scene.preview API endpoint if available,
 * otherwise shows a clear status with a "Request Preview" fallback.
 */
import { useState } from 'react';
import type { JobInspect } from '../api/types';

interface SceneBlock {
  scene_index: number;
  start_sec: number;
  end_sec: number;
  narrative_role?: string;
  asset_label?: string;
}

function parseScenes(inspect: JobInspect): SceneBlock[] {
  try {
    const raw = (inspect.manifest as Record<string, unknown>)?.scenes_json ??
                (inspect.job as Record<string, unknown>)?.scenes_json;
    if (typeof raw === 'string') {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return parsed as SceneBlock[];
    }
  } catch { /* ignore */ }
  const dur = Number(
    (inspect.job as Record<string, unknown>)?.narration_duration_sec ??
    (inspect.job as Record<string, unknown>)?.total_duration_sec ?? 0
  ) || 30;
  return [{ scene_index: 0, start_sec: 0, end_sec: dur, narrative_role: 'BODY', asset_label: 'Primary clip' }];
}

type PreviewState = 'idle' | 'requesting' | 'ready' | 'error';

export default function ScenePreviewPanel({ inspect }: { inspect: JobInspect }) {
  const scenes = parseScenes(inspect);
  const [selectedScene, setSelectedScene] = useState(0);
  const [previewState, setPreviewState] = useState<PreviewState>('idle');
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const requestPreview = async () => {
    setPreviewState('requesting');
    setErrorMsg(null);
    setPreviewPath(null);

    try {
      // Try to call the backend scene preview endpoint
      const res = await fetch('http://127.0.0.1:8089/api/scene.preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: inspect.job_id, scene_index: selectedScene }),
      });
      if (res.ok) {
        const data = await res.json() as { preview_path?: string; error?: string };
        if (data.preview_path) {
          setPreviewPath(data.preview_path);
          setPreviewState('ready');
        } else {
          throw new Error(data.error ?? 'No preview path returned');
        }
      } else {
        throw new Error('Preview endpoint returned ' + res.status);
      }
    } catch (e) {
      // Backend endpoint may not be wired yet; surface friendly message
      setErrorMsg(
        'Scene preview unavailable: ' +
        (e instanceof Error ? e.message : String(e)) +
        '. The scene.preview endpoint is a Phase 5 backend extension.'
      );
      setPreviewState('error');
    }
  };

  const scene = scenes[selectedScene] ?? scenes[0];

  return (
    <div className='card scene-preview-card'>
      <div className='card-header'>
        <span>Scene Preview Renderer</span>
        <span className='muted small'>Single-scene render in &lt;3s</span>
      </div>
      <div className='card-body scene-preview-body'>

        {/* Scene selector */}
        <div className='scene-preview-selector'>
          <label htmlFor='scene-preview-select' className='creator-field-label'>Select Scene</label>
          <select
            id='scene-preview-select'
            className='select-input'
            value={selectedScene}
            onChange={e => {
              setSelectedScene(Number(e.target.value));
              setPreviewState('idle');
              setPreviewPath(null);
              setErrorMsg(null);
            }}
            aria-label='Scene to preview'
          >
            {scenes.map(s => (
              <option key={s.scene_index} value={s.scene_index}>
                Scene {s.scene_index} — {s.narrative_role ?? 'BODY'} ({s.start_sec.toFixed(1)}s – {s.end_sec.toFixed(1)}s)
              </option>
            ))}
          </select>
        </div>

        {/* Scene info */}
        {scene && (
          <dl className='kv' style={{ marginBottom: '12px' }}>
            <dt>Asset</dt><dd>{scene.asset_label ?? '\u2014'}</dd>
            <dt>Duration</dt><dd>{(scene.end_sec - scene.start_sec).toFixed(1)}s</dd>
            <dt>Role</dt><dd>{scene.narrative_role ?? 'BODY'}</dd>
          </dl>
        )}

        {/* Preview trigger */}
        {previewState === 'idle' && (
          <button
            id='btn-request-scene-preview'
            className='primary-btn'
            onClick={requestPreview}
          >
            \u25b6 Render Scene Preview
          </button>
        )}

        {previewState === 'requesting' && (
          <div className='banner banner-info'>
            Rendering scene {selectedScene}\u2026 (target: &lt;3s)
          </div>
        )}

        {previewState === 'ready' && previewPath && (
          <div className='scene-preview-result'>
            <div className='banner banner-info' style={{ marginBottom: '8px' }}>
              Preview ready: {previewPath.split('/').pop() ?? previewPath}
            </div>
            <a
              href={'file:///' + previewPath.replace(/\\/g, '/')}
              target='_blank'
              rel='noopener noreferrer'
              className='ghost-btn'
            >
              Open Preview
            </a>
            <button
              id='btn-re-render-scene-preview'
              className='ghost-btn'
              onClick={() => { setPreviewState('idle'); setPreviewPath(null); }}
              style={{ marginLeft: '8px' }}
            >
              Re-render
            </button>
          </div>
        )}

        {previewState === 'error' && (
          <div>
            <div className='banner banner-warn'>{errorMsg}</div>
            <button
              id='btn-retry-scene-preview'
              className='ghost-btn'
              style={{ marginTop: '8px' }}
              onClick={() => setPreviewState('idle')}
            >
              Dismiss
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
