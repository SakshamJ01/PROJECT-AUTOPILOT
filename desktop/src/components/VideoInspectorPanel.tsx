/**
 * Phase 5 -- Multi-Track Video Inspector
 */
import { useState, useMemo } from 'react';
import type { JobInspect } from '../api/types';

function fmtSec(s: number): string { return s.toFixed(1) + 's'; }

interface SceneBlock {
  scene_index: number;
  start_sec: number;
  end_sec: number;
  narrative_role?: string;
  asset_label?: string;
  caption_position?: string;
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
  return [{ scene_index: 0, start_sec: 0, end_sec: dur, narrative_role: 'BODY', asset_label: 'Primary clip', caption_position: 'LOWER' }];
}

const TRACKS = [
  { id: 'voice',    label: 'VOICE',    color: '#3d7dd8' },
  { id: 'captions', label: 'CAPTIONS', color: '#9b59b6' },
  { id: 'visuals',  label: 'VISUALS',  color: '#2ec27e' },
  { id: 'bgm',      label: 'BGM',      color: '#e6a23c' },
  { id: 'sfx',      label: 'SFX',      color: '#e5484d' },
] as const;

function TrackRow({ label, color, scenes, totalDur, trackId, activeScene, onSceneClick }:
  { label: string; color: string; scenes: SceneBlock[]; totalDur: number; trackId: string; activeScene: number | null; onSceneClick: (i: number) => void }) {
  return (
    <div className='inspector-track-row' aria-label={label + ' track'}>
      <div className='inspector-track-label' style={{ color }}>{label}</div>
      <div className='inspector-track-bar'>
        {scenes.map(s => {
          const left = (s.start_sec / totalDur) * 100;
          const width = ((s.end_sec - s.start_sec) / totalDur) * 100;
          const isActive = activeScene === s.scene_index;
          return (
            <button
              key={s.scene_index}
              id={'inspector-' + trackId + '-scene-' + s.scene_index}
              className={'inspector-block inspector-block-' + trackId + (isActive ? ' inspector-block-active' : '')}
              style={{ left: left + '%', width: Math.max(width, 1.5) + '%', background: isActive ? color : color + '55', borderColor: color }}
              onClick={() => onSceneClick(s.scene_index)}
              title={trackId === 'visuals' ? (s.asset_label ?? 'Scene ' + s.scene_index) : fmtSec(s.start_sec) + ' - ' + fmtSec(s.end_sec)}
            >
              {trackId === 'visuals' && width > 8 ? <span className='inspector-block-label'>{s.narrative_role ?? 'S' + s.scene_index}</span> : null}
            </button>
          );
        })}
        {trackId === 'voice' && Array.from({ length: 11 }).map((_, i) => (
          <span key={i} className='inspector-tick' style={{ left: (i / 10 * 100) + '%' }} aria-hidden>{fmtSec((i / 10) * totalDur)}</span>
        ))}
      </div>
    </div>
  );
}

function SceneDetail({ scene }: { scene: SceneBlock }) {
  return (
    <div className='inspector-scene-detail'>
      <div className='inspector-scene-detail-header'>Scene {scene.scene_index} {'\u2014'} {scene.narrative_role ?? '\u2014'}</div>
      <dl className='kv'>
        <dt>Start</dt><dd>{fmtSec(scene.start_sec)}</dd>
        <dt>End</dt><dd>{fmtSec(scene.end_sec)}</dd>
        <dt>Duration</dt><dd>{fmtSec(scene.end_sec - scene.start_sec)}</dd>
        <dt>Asset</dt><dd>{scene.asset_label ?? '\u2014'}</dd>
        <dt>Caption Position</dt><dd>{scene.caption_position ?? 'LOWER'}</dd>
      </dl>
    </div>
  );
}

export default function VideoInspectorPanel({ inspect }: { inspect: JobInspect }) {
  const scenes = useMemo(() => parseScenes(inspect), [inspect]);
  const [activeScene, setActiveScene] = useState<number | null>(null);
  const totalDur = useMemo(() => scenes.length === 0 ? 30 : Math.max(...scenes.map(s => s.end_sec)), [scenes]);
  const selected = scenes.find(s => s.scene_index === activeScene) ?? null;
  return (
    <div className='card inspector-card'>
      <div className='card-header'>
        <span>Multi-Track Video Inspector</span>
        <span className='muted small'>{scenes.length} scenes {'\u00b7'} {fmtSec(totalDur)}</span>
      </div>
      <div className='card-body inspector-body'>
        <div className='inspector-tracks' aria-label='Multi-track video timeline'>
          {TRACKS.map(t => (
            <TrackRow key={t.id} label={t.label} color={t.color} scenes={scenes} totalDur={totalDur} trackId={t.id} activeScene={activeScene} onSceneClick={i => setActiveScene(p => p === i ? null : i)} />
          ))}
        </div>
        {selected ? <SceneDetail scene={selected} /> : <p className='muted small' style={{ marginTop: '10px' }}>Click any scene block to inspect per-scene details.</p>}
      </div>
    </div>
  );
}
