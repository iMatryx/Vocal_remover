import { useEffect, useRef, useState } from 'react';

function AudioPreviewPlayer({
  taskId,
  apiBaseUrl = '',
  onPlayStart = null,  // Callback when playback starts (for Task 4.4)
  onPlayStop = null,   // Callback when playback stops (for Task 4.4)
  isActive = false,    // Is this player the active one (for Task 4.4)
  previewSpeed = 1.0,  // Speed for preview playback (Task 5.5)
  onDurationChange = null,  // Reports loaded track duration to parent (for Task 5.4)
  seekTo = null,  // { time, requestId } - external seek request (for Task 6.3)
  onTimeUpdate = null,  // Reports live playback position to parent (for waveform playhead)
}) {
  const [previewType, setPreviewType] = useState('both');
  const [isLoading, setIsLoading] = useState(false);
  const [isBuffering, setIsBuffering] = useState(false);
  const [previewLoaded, setPreviewLoaded] = useState(false);
  const [connectionInterrupted, setConnectionInterrupted] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(1);
  const [error, setError] = useState('');
  const [displayedSpeed, setDisplayedSpeed] = useState(previewSpeed);
  const audioRef = useRef(null);
  const canvasRef = useRef(null);
  const analyserRef = useRef(null);
  const isPlayingRef = useRef(false);

  if (!taskId) {
    return null;
  }

  const previewOptions = [
    { value: 'original', label: 'Preview Original', emoji: '🎧' },
    { value: 'vocals', label: 'Preview Vocals', emoji: '🎤' },
    { value: 'accompaniment', label: 'Preview Instrumental', emoji: '🎸' },
    { value: 'both', label: 'Preview Both (Mixed)', emoji: '🎵' },
  ];

  // Apply preview speed to audio element (Task 5.5)
  useEffect(() => {
    if (!audioRef.current) return;
    audioRef.current.playbackRate = previewSpeed;
    setDisplayedSpeed(previewSpeed);
  }, [previewSpeed]);

  // External seek request from WaveformComparison (Task 6.3)
  useEffect(() => {
    if (!audioRef.current || !seekTo) return;
    audioRef.current.currentTime = seekTo.time;
    setCurrentTime(seekTo.time);
  }, [seekTo]);

  // Setup Web Audio API for waveform visualization. Runs once per mount,
  // not per isActive change: createMediaElementAudioSource can only be
  // called once ever for a given <audio> element - calling it again (e.g.
  // after pausing and pressing play again) throws InvalidStateError.
  useEffect(() => {
    if (!audioRef.current || !canvasRef.current) return;

    let audioContext;
    try {
      audioContext = new (window.AudioContext || window.webkitAudioContext)();
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 256;

      const source = audioContext.createMediaElementAudioSource(audioRef.current);
      source.connect(analyser);
      analyser.connect(audioContext.destination);

      analyserRef.current = analyser;
    } catch (e) {
      console.warn('Web Audio API not available:', e);
    }

    return () => {
      analyserRef.current = null;
      audioContext?.close();
    };
  }, []);

  // Draw waveform visualization
  const drawWaveform = () => {
    if (!canvasRef.current || !analyserRef.current) return;

    const canvas = canvasRef.current;
    const analyser = analyserRef.current;
    const ctx = canvas.getContext('2d');

    const bufferLength = analyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);
    analyser.getByteFrequencyData(dataArray);

    ctx.fillStyle = 'rgb(15, 23, 42)';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    ctx.lineWidth = 2;
    ctx.strokeStyle = 'rgb(59, 130, 246)';
    ctx.beginPath();

    const sliceWidth = canvas.width / bufferLength;
    let x = 0;

    for (let i = 0; i < bufferLength; i++) {
      const v = dataArray[i] / 128.0;
      const y = (v * canvas.height) / 2;

      if (i === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }

      x += sliceWidth;
    }

    ctx.lineTo(canvas.width, canvas.height / 2);
    ctx.stroke();

    if (isPlayingRef.current) {
      requestAnimationFrame(drawWaveform);
    }
  };

  // Handle changes in preview type. isPlaying/onPlayStop are updated by the
  // native onPause handler below, not set manually here - pause() always
  // fires a real "pause" event, so there's one source of truth.
  const handlePreviewTypeChange = (type) => {
    if (audioRef.current) {
      audioRef.current.pause();
    }
    setPreviewType(type);
    setPreviewLoaded(false);
    setConnectionInterrupted(false);
  };

  // Handle play/pause. Only ever calls the DOM audio methods - isPlaying
  // itself is derived solely from the <audio> element's own onPlay/onPause/
  // onEnded events below, so this can never desync from what's actually
  // playing (e.g. if play() is rejected or interrupted).
  const handlePlayPause = () => {
    if (!audioRef.current || isLoading) return;

    if (isPlaying) {
      audioRef.current.pause();
    } else {
      // Notify parent that this player is starting playback
      if (onPlayStart) onPlayStart();

      setIsBuffering(true);
      audioRef.current.play().catch((err) => {
        console.error('Playback error:', err);
        setError('Could not start playback');
        setIsBuffering(false);
      });
    }
  };

  // Handle seeking
  const handleTimeChange = (e) => {
    const time = parseFloat(e.target.value);
    if (audioRef.current) {
      audioRef.current.currentTime = time;
      setCurrentTime(time);
    }
  };

  // Handle volume change
  const handleVolumeChange = (e) => {
    const vol = parseFloat(e.target.value);
    if (audioRef.current) {
      audioRef.current.volume = vol;
    }
    setVolume(vol);
  };

  // Format time display
  const formatTime = (seconds) => {
    if (!seconds || isNaN(seconds)) return '0:00';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  const previewUrl = `${apiBaseUrl}/api/preview/${taskId}?file_type=${previewType}`;

  return (
    <div className={`rounded-2xl border p-6 transition-all ${
      isActive && isPlaying
        ? 'border-primary-400 bg-primary-500/10 shadow-lg shadow-primary-500/20'
        : 'border-slate-700 bg-slate-900/70'
    }`}>
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-lg font-semibold">Preview Audio</h3>
        {isActive && isPlaying && (
          <div className="flex items-center gap-2">
            <div className="h-2 w-2 animate-pulse rounded-full bg-primary-500" />
            <span className="text-xs font-semibold text-primary-300">Now Playing</span>
          </div>
        )}
      </div>

      {/* Preview Type Selector (3 Buttons - Task 4.3) */}
      <div className="mb-6 space-y-2">
        {previewOptions.map((option) => (
          <button
            key={option.value}
            onClick={() => handlePreviewTypeChange(option.value)}
            disabled={isLoading || isBuffering}
            className={`w-full rounded-lg px-4 py-3 text-sm font-medium transition-all ${
              isLoading || isBuffering
                ? 'cursor-not-allowed border border-slate-700 bg-slate-800/50 text-slate-500 opacity-50'
                : previewType === option.value
                  ? 'border-primary-400 bg-primary-500/20 text-primary-200 ring-2 ring-primary-500/50'
                  : 'border border-slate-600 bg-slate-800 text-slate-300 hover:border-slate-500 hover:bg-slate-700'
            }`}
            title={
              isLoading || isBuffering
                ? 'Wait for preview to load...'
                : option.label
            }
          >
            <span className="mr-2">{option.emoji}</span>
            {option.label}
          </button>
        ))}
      </div>

      {/* Audio Player - Task 4.3 */}
      <div className="mb-6 rounded-lg border border-slate-600 bg-slate-800/50 p-4">
        {/* Hidden Audio Element */}
        <audio
          ref={audioRef}
          src={previewUrl}
          onLoadStart={() => {
            setIsLoading(true);
            setIsBuffering(true);
            setConnectionInterrupted(false);
            setError('');
          }}
          onLoadedMetadata={(e) => {
            setDuration(e.target.duration);
            setPreviewLoaded(true);
            setError('');
            if (onDurationChange) onDurationChange(e.target.duration);
          }}
          onCanPlay={() => {
            setIsBuffering(false);
            setIsLoading(false);
          }}
          onPlay={() => {
            isPlayingRef.current = true;
            setIsPlaying(true);
            drawWaveform();
          }}
          onPause={() => {
            isPlayingRef.current = false;
            setIsPlaying(false);
            if (onPlayStop) onPlayStop();
          }}
          onTimeUpdate={(e) => {
            setCurrentTime(e.target.currentTime);
            if (onTimeUpdate) onTimeUpdate(e.target.currentTime);
          }}
          onWaiting={() => setIsBuffering(true)}
          onPlaying={() => setIsBuffering(false)}
          onEnded={() => {
            isPlayingRef.current = false;
            setIsPlaying(false);
            if (onPlayStop) onPlayStop();
          }}
          onError={(e) => {
            setError('Could not load audio preview');
            isPlayingRef.current = false;
            setIsPlaying(false);
            setIsLoading(false);
            setIsBuffering(false);
            setConnectionInterrupted(true);
          }}
          onAbort={() => setIsBuffering(false)}
          crossOrigin="anonymous"
        />

        {/* Waveform Visualization (Task 4.5) */}
        <div className="mb-4 rounded-lg border border-slate-600 bg-slate-950 p-2">
          <canvas
            ref={canvasRef}
            width={300}
            height={60}
            className="w-full rounded"
          />
          <div className="mt-2 text-center text-xs text-slate-400">
            {isLoading && <span className="text-yellow-400">⏳ Loading preview...</span>}
            {previewLoaded && !isLoading && !connectionInterrupted && (
              <span className="text-green-400">✓ Preview loaded</span>
            )}
            {connectionInterrupted && (
              <span className="text-red-400">⚠ Stream interrupted</span>
            )}
            {isBuffering && isPlaying && (
              <span className="text-yellow-400">🔄 Buffering...</span>
            )}
          </div>
        </div>

        {/* Controls */}
        <div className="mb-4 flex items-center justify-between gap-4">
          {/* Play/Pause Button. Only disabled while the initial metadata
              load hasn't finished - buffering mid-playback must never block
              the pause control, or a stuck buffering flag (e.g. after a
              stream hiccup) would make the player unstoppable. */}
          <button
            onClick={handlePlayPause}
            disabled={isLoading}
            className={`flex h-12 w-12 items-center justify-center rounded-full text-lg font-bold transition-all ${
              !isLoading
                ? 'bg-primary-600 text-white hover:bg-primary-500'
                : 'cursor-not-allowed bg-slate-700 text-slate-500 opacity-50'
            }`}
            title={isLoading ? 'Loading preview...' : isBuffering ? 'Buffering - click to pause' : 'Play/Pause'}
          >
            {isLoading ? (
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-white" />
            ) : isPlaying ? (
              '⏸'
            ) : (
              '▶'
            )}
          </button>

          {/* Time Display */}
          <div className="text-sm font-mono text-slate-300">
            {formatTime(currentTime)} / {formatTime(duration)}
          </div>

          {/* Volume Control */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-400">🔊</span>
            <input
              type="range"
              min="0"
              max="1"
              step="0.1"
              value={volume}
              onChange={handleVolumeChange}
              className="w-20 cursor-pointer"
              disabled={!isActive}
            />
          </div>
        </div>

        {/* Progress Bar */}
        <div className="mb-2">
          <input
            type="range"
            min="0"
            max={duration || 0}
            value={currentTime}
            onChange={handleTimeChange}
            className="w-full cursor-pointer"
            disabled={!isActive}
          />
        </div>

        {/* Status */}
        {error && (
          <p className="text-xs text-red-400">{error}</p>
        )}
        {isPlaying && !error && isActive && (
          <p className="text-xs text-green-400">▶ Playing {previewType}...</p>
        )}
        {!isActive && (
          <p className="text-xs text-slate-500">Click play to activate this preview</p>
        )}
      </div>

      {/* Info & Speed Display */}
      <div className="rounded-lg border border-slate-600 bg-slate-800/50 p-3">
        <div className="flex items-center justify-between">
          <p className="text-xs text-slate-400">
            <span className="font-semibold">💡 Tip:</span> Only one preview can play at a time. Select different buttons to switch preview types.
          </p>
          {displayedSpeed !== 1.0 && (
            <div className="flex items-center gap-1 rounded-full bg-primary-500/20 px-2 py-1">
              <span className="text-xs font-semibold text-primary-300">{displayedSpeed}x</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default AudioPreviewPlayer;
