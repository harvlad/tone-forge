/**
 * WaveformTrimmer - Shared waveform component for ToneForge
 *
 * Two modes:
 * - 'trim': Draggable handles for selecting audio region before analysis
 * - 'display': Shows quality overlays after analysis (no handles)
 */

const WaveformTrimmer = (function() {
    // Store per-canvas state
    const instances = new Map();

    // Minimum selection duration in seconds
    const MIN_SELECTION_SEC = 5;

    // Handle hit area width in pixels
    const HANDLE_WIDTH = 12;
    const HANDLE_HIT_AREA = 20;

    /**
     * Initialize waveform trimmer on a canvas
     * @param {HTMLCanvasElement} canvas - The canvas element
     * @param {Object} waveformData - Waveform peaks data from API
     * @param {Object} options - Configuration options
     * @param {string} options.mode - 'trim' or 'display'
     * @param {Function} options.onChange - Callback when selection changes (trim mode)
     * @param {Object} options.quality - Quality data for overlays (display mode)
     * @param {number} options.initialStart - Initial start time (seconds)
     * @param {number} options.initialEnd - Initial end time (seconds)
     */
    function init(canvas, waveformData, options = {}) {
        const mode = options.mode || 'display';
        const duration = waveformData.duration_sec || 1;

        const state = {
            canvas,
            ctx: canvas.getContext('2d'),
            waveform: waveformData,
            duration,
            mode,
            quality: options.quality || {},
            onChange: options.onChange || (() => {}),
            // Selection state (0-1 normalized)
            selectionStart: (options.initialStart || 0) / duration,
            selectionEnd: (options.initialEnd || duration) / duration,
            // Interaction state
            dragging: null, // 'start', 'end', or 'region'
            dragOffset: 0,
            hoverHandle: null,
            hoverX: null,
            // Audio playback
            audioElement: null,
            isPlaying: false,
            playbackPosition: 0,
            animationFrame: null,
        };

        // Ensure valid selection
        state.selectionStart = Math.max(0, Math.min(1, state.selectionStart));
        state.selectionEnd = Math.max(0, Math.min(1, state.selectionEnd));
        if (state.selectionEnd <= state.selectionStart) {
            state.selectionEnd = Math.min(1, state.selectionStart + MIN_SELECTION_SEC / duration);
        }

        instances.set(canvas, state);

        // Setup canvas and event listeners
        setupCanvas(state);
        if (mode === 'trim') {
            setupTrimInteractions(state);
        } else {
            setupDisplayInteractions(state);
        }

        // Initial draw
        draw(state);

        return {
            getSelection: () => getSelection(state),
            setSelection: (start, end) => setSelection(state, start, end),
            setAudioElement: (audio) => setAudioElement(state, audio),
            destroy: () => destroy(state),
        };
    }

    function setupCanvas(state) {
        const { canvas } = state;
        const rect = canvas.getBoundingClientRect();
        const dpr = window.devicePixelRatio || 1;
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
        state.ctx.scale(dpr, dpr);
        state.width = rect.width;
        state.height = rect.height;

        // Handle resize
        const resizeObserver = new ResizeObserver(() => {
            const newRect = canvas.getBoundingClientRect();
            const dpr = window.devicePixelRatio || 1;
            canvas.width = newRect.width * dpr;
            canvas.height = newRect.height * dpr;
            state.ctx = canvas.getContext('2d');
            state.ctx.scale(dpr, dpr);
            state.width = newRect.width;
            state.height = newRect.height;
            draw(state);
        });
        resizeObserver.observe(canvas);
        state.resizeObserver = resizeObserver;
    }

    function setupTrimInteractions(state) {
        const { canvas } = state;

        canvas.style.cursor = 'default';

        canvas.addEventListener('mousedown', (e) => onMouseDown(state, e));
        canvas.addEventListener('mousemove', (e) => onMouseMove(state, e));
        canvas.addEventListener('mouseup', (e) => onMouseUp(state, e));
        canvas.addEventListener('mouseleave', (e) => onMouseLeave(state, e));

        // Touch support
        canvas.addEventListener('touchstart', (e) => onTouchStart(state, e), { passive: false });
        canvas.addEventListener('touchmove', (e) => onTouchMove(state, e), { passive: false });
        canvas.addEventListener('touchend', (e) => onTouchEnd(state, e));
    }

    function setupDisplayInteractions(state) {
        const { canvas } = state;

        canvas.addEventListener('mousemove', (e) => {
            const rect = canvas.getBoundingClientRect();
            state.hoverX = e.clientX - rect.left;
            draw(state);
        });

        canvas.addEventListener('mouseleave', () => {
            state.hoverX = null;
            draw(state);
        });
    }

    function getMouseX(state, e) {
        const rect = state.canvas.getBoundingClientRect();
        return e.clientX - rect.left;
    }

    function getTouchX(state, e) {
        const rect = state.canvas.getBoundingClientRect();
        return e.touches[0].clientX - rect.left;
    }

    function getHitTarget(state, x) {
        const { width, selectionStart, selectionEnd } = state;
        const startX = selectionStart * width;
        const endX = selectionEnd * width;

        // Check handles first (higher priority)
        if (Math.abs(x - startX) <= HANDLE_HIT_AREA) return 'start';
        if (Math.abs(x - endX) <= HANDLE_HIT_AREA) return 'end';

        // Check if inside selection region
        if (x > startX + HANDLE_HIT_AREA && x < endX - HANDLE_HIT_AREA) return 'region';

        return null;
    }

    function onMouseDown(state, e) {
        const x = getMouseX(state, e);
        const target = getHitTarget(state, x);

        if (target) {
            state.dragging = target;
            if (target === 'region') {
                state.dragOffset = x / state.width - state.selectionStart;
            }
            e.preventDefault();
        }
    }

    function onMouseMove(state, e) {
        const x = getMouseX(state, e);

        if (state.dragging) {
            handleDrag(state, x);
        } else {
            // Update cursor based on hover
            const target = getHitTarget(state, x);
            state.hoverHandle = target;
            state.canvas.style.cursor = target === 'start' || target === 'end' ? 'ew-resize' :
                                         target === 'region' ? 'grab' : 'default';
        }

        state.hoverX = x;
        draw(state);
    }

    function onMouseUp(state, e) {
        if (state.dragging) {
            state.dragging = null;
            state.onChange(getSelection(state));
        }
    }

    function onMouseLeave(state, e) {
        state.dragging = null;
        state.hoverHandle = null;
        state.hoverX = null;
        state.canvas.style.cursor = 'default';
        draw(state);
    }

    function onTouchStart(state, e) {
        if (e.touches.length === 1) {
            const x = getTouchX(state, e);
            const target = getHitTarget(state, x);
            if (target) {
                state.dragging = target;
                if (target === 'region') {
                    state.dragOffset = x / state.width - state.selectionStart;
                }
                e.preventDefault();
            }
        }
    }

    function onTouchMove(state, e) {
        if (state.dragging && e.touches.length === 1) {
            const x = getTouchX(state, e);
            handleDrag(state, x);
            e.preventDefault();
        }
    }

    function onTouchEnd(state, e) {
        if (state.dragging) {
            state.dragging = null;
            state.onChange(getSelection(state));
        }
    }

    function handleDrag(state, x) {
        const { width, duration } = state;
        const minSelection = MIN_SELECTION_SEC / duration;
        let normalized = x / width;
        normalized = Math.max(0, Math.min(1, normalized));

        if (state.dragging === 'start') {
            state.selectionStart = Math.min(normalized, state.selectionEnd - minSelection);
            state.selectionStart = Math.max(0, state.selectionStart);
        } else if (state.dragging === 'end') {
            state.selectionEnd = Math.max(normalized, state.selectionStart + minSelection);
            state.selectionEnd = Math.min(1, state.selectionEnd);
        } else if (state.dragging === 'region') {
            const regionWidth = state.selectionEnd - state.selectionStart;
            let newStart = normalized - state.dragOffset;
            newStart = Math.max(0, Math.min(1 - regionWidth, newStart));
            state.selectionStart = newStart;
            state.selectionEnd = newStart + regionWidth;
        }

        draw(state);
    }

    function draw(state) {
        const { ctx, width, height, waveform, mode, quality, duration } = state;
        const centerY = height / 2;

        // Clear canvas
        const bgColor = getComputedStyle(document.documentElement).getPropertyValue('--bg-input') || '#0f0f1a';
        ctx.fillStyle = bgColor;
        ctx.fillRect(0, 0, width, height);

        if (!waveform || !waveform.peaks_positive || waveform.peaks_positive.length === 0) {
            return;
        }

        const peaks_pos = waveform.peaks_positive;
        const peaks_neg = waveform.peaks_negative;
        const rms = waveform.rms || [];
        const numPoints = peaks_pos.length;
        const pointWidth = width / numPoints;

        // Selection bounds in pixels
        const selStartX = state.selectionStart * width;
        const selEndX = state.selectionEnd * width;

        // In display mode, show quality overlays first
        if (mode === 'display') {
            drawQualityOverlays(state);
        }

        // Draw waveform with different colors inside/outside selection
        for (let i = 0; i < numPoints; i++) {
            const x = i * pointWidth;
            const peakPos = peaks_pos[i] || 0;
            const peakNeg = peaks_neg[i] || 0;
            const rmsVal = rms[i] || 0;

            const isInSelection = mode === 'trim' ? (x >= selStartX && x <= selEndX) : true;

            // Draw peaks (outer envelope)
            const peakY1 = centerY - (peakPos * centerY * 0.95);
            const peakY2 = centerY - (peakNeg * centerY * 0.95);
            const peakHeight = peakY2 - peakY1;

            if (isInSelection) {
                ctx.fillStyle = 'rgba(96, 165, 250, 0.6)';  // Blue for selected
            } else {
                ctx.fillStyle = 'rgba(100, 100, 100, 0.4)';  // Gray for unselected
            }
            ctx.fillRect(x, peakY1, Math.max(pointWidth - 0.5, 1), peakHeight);

            // Draw RMS (inner area)
            if (rmsVal > 0) {
                const rmsHeight = rmsVal * centerY * 0.95;
                const rmsY1 = centerY - rmsHeight;
                const rmsBarHeight = rmsHeight * 2;

                if (isInSelection) {
                    ctx.fillStyle = 'rgba(251, 146, 60, 0.85)';  // Orange for selected
                } else {
                    ctx.fillStyle = 'rgba(120, 120, 120, 0.5)';  // Darker gray for unselected
                }
                ctx.fillRect(x, rmsY1, Math.max(pointWidth - 0.5, 1), rmsBarHeight);
            }
        }

        // Draw center line
        ctx.beginPath();
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
        ctx.lineWidth = 1;
        ctx.moveTo(0, centerY);
        ctx.lineTo(width, centerY);
        ctx.stroke();

        // Draw trim handles and selection UI in trim mode
        if (mode === 'trim') {
            drawTrimUI(state, selStartX, selEndX);
        }

        // Draw hover tooltip
        if (state.hoverX !== null) {
            drawHoverTooltip(state);
        }

        // Draw playback position
        if (state.isPlaying && state.playbackPosition !== null) {
            drawPlaybackPosition(state);
        }

        // Draw legend
        drawLegend(state);
    }

    function drawQualityOverlays(state) {
        const { ctx, width, height, quality, duration } = state;

        // Contamination regions (yellow)
        if (quality.contamination?.regions) {
            ctx.fillStyle = 'rgba(251, 191, 36, 0.15)';
            for (const region of quality.contamination.regions) {
                const startX = (region.start / duration) * width;
                const endX = (region.end / duration) * width;
                ctx.fillRect(startX, 0, Math.max(2, endX - startX), height);
            }
        }

        // Artifact regions (red)
        if (quality.artifacts?.regions) {
            ctx.fillStyle = 'rgba(248, 113, 113, 0.15)';
            for (const region of quality.artifacts.regions) {
                const startX = (region.start / duration) * width;
                const endX = (region.end / duration) * width;
                ctx.fillRect(startX, 0, Math.max(2, endX - startX), height);
            }
        }
    }

    function drawTrimUI(state, selStartX, selEndX) {
        const { ctx, width, height, duration, hoverHandle, dragging } = state;

        // Selection overlay (darken outside)
        ctx.fillStyle = 'rgba(0, 0, 0, 0.4)';
        ctx.fillRect(0, 0, selStartX, height);
        ctx.fillRect(selEndX, 0, width - selEndX, height);

        // Selection border
        ctx.strokeStyle = 'rgba(251, 191, 36, 0.8)';
        ctx.lineWidth = 2;
        ctx.strokeRect(selStartX, 0, selEndX - selStartX, height);

        // Draw handles
        const handleColor = 'rgba(251, 191, 36, 1)';  // Yellow/gold
        const handleHighlight = 'rgba(251, 191, 36, 1)';

        // Start handle
        const startHandleActive = hoverHandle === 'start' || dragging === 'start';
        ctx.fillStyle = startHandleActive ? handleHighlight : handleColor;
        ctx.fillRect(selStartX - HANDLE_WIDTH / 2, 0, HANDLE_WIDTH, height);

        // Handle grip lines (start)
        ctx.strokeStyle = 'rgba(0, 0, 0, 0.5)';
        ctx.lineWidth = 1;
        for (let i = -2; i <= 2; i++) {
            const lineX = selStartX + i * 2;
            ctx.beginPath();
            ctx.moveTo(lineX, height / 2 - 10);
            ctx.lineTo(lineX, height / 2 + 10);
            ctx.stroke();
        }

        // End handle
        const endHandleActive = hoverHandle === 'end' || dragging === 'end';
        ctx.fillStyle = endHandleActive ? handleHighlight : handleColor;
        ctx.fillRect(selEndX - HANDLE_WIDTH / 2, 0, HANDLE_WIDTH, height);

        // Handle grip lines (end)
        for (let i = -2; i <= 2; i++) {
            const lineX = selEndX + i * 2;
            ctx.beginPath();
            ctx.moveTo(lineX, height / 2 - 10);
            ctx.lineTo(lineX, height / 2 + 10);
            ctx.stroke();
        }

        // Time labels at handles
        ctx.font = '11px system-ui, sans-serif';
        ctx.fillStyle = 'rgba(255, 255, 255, 0.9)';
        ctx.textAlign = 'center';

        const startTime = state.selectionStart * duration;
        const endTime = state.selectionEnd * duration;
        const selectionDuration = endTime - startTime;

        // Start time label
        ctx.fillText(formatTime(startTime), selStartX, height - 5);

        // End time label
        ctx.fillText(formatTime(endTime), selEndX, height - 5);

        // Duration in center of selection
        const centerX = (selStartX + selEndX) / 2;
        ctx.fillStyle = 'rgba(251, 191, 36, 1)';
        ctx.fillText(`${formatTime(selectionDuration)} selected`, centerX, 15);
    }

    function drawHoverTooltip(state) {
        const { ctx, width, height, waveform, duration, hoverX, mode } = state;

        // Don't show tooltip when dragging in trim mode
        if (mode === 'trim' && state.dragging) return;

        const peaks_pos = waveform.peaks_positive;
        const peaks_neg = waveform.peaks_negative;
        const rms = waveform.rms || [];
        const numPoints = peaks_pos.length;

        const idx = Math.floor((hoverX / width) * numPoints);
        if (idx < 0 || idx >= numPoints) return;

        const timeAtPos = (hoverX / width) * duration;
        const peakVal = Math.max(Math.abs(peaks_pos[idx] || 0), Math.abs(peaks_neg[idx] || 0));
        const rmsVal = rms[idx] || 0;
        const peakDb = peakVal > 0 ? (20 * Math.log10(peakVal)).toFixed(1) : '-inf';
        const rmsDb = rmsVal > 0 ? (20 * Math.log10(rmsVal)).toFixed(1) : '-inf';

        // Vertical line
        ctx.beginPath();
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.5)';
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.moveTo(hoverX, 0);
        ctx.lineTo(hoverX, height);
        ctx.stroke();
        ctx.setLineDash([]);

        // Tooltip background
        const tooltipWidth = 100;
        const tooltipHeight = 50;
        const tooltipX = hoverX < width / 2 ? hoverX + 10 : hoverX - tooltipWidth - 10;
        const tooltipY = height - tooltipHeight - 5;

        ctx.fillStyle = 'rgba(0, 0, 0, 0.85)';
        ctx.fillRect(tooltipX, tooltipY, tooltipWidth, tooltipHeight);
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.3)';
        ctx.strokeRect(tooltipX, tooltipY, tooltipWidth, tooltipHeight);

        // Tooltip text
        ctx.font = '10px monospace';
        ctx.textAlign = 'left';
        ctx.fillStyle = 'rgba(255, 255, 255, 0.9)';
        ctx.fillText(`Time: ${timeAtPos.toFixed(2)}s`, tooltipX + 5, tooltipY + 15);
        ctx.fillStyle = 'rgba(96, 165, 250, 1)';
        ctx.fillText(`Peak: ${peakDb} dB`, tooltipX + 5, tooltipY + 29);
        ctx.fillStyle = 'rgba(251, 146, 60, 1)';
        ctx.fillText(`RMS:  ${rmsDb} dB`, tooltipX + 5, tooltipY + 43);
    }

    function drawPlaybackPosition(state) {
        const { ctx, width, height, playbackPosition, duration, selectionStart } = state;

        // Convert playback position to canvas position
        const normalizedPos = selectionStart + (playbackPosition / duration) * (state.selectionEnd - selectionStart);
        const x = normalizedPos * width;

        ctx.beginPath();
        ctx.strokeStyle = 'rgba(74, 222, 128, 1)';  // Green playhead
        ctx.lineWidth = 2;
        ctx.moveTo(x, 0);
        ctx.lineTo(x, height);
        ctx.stroke();
    }

    function drawLegend(state) {
        const { ctx, width, mode, quality } = state;

        ctx.font = '10px system-ui, sans-serif';
        ctx.textAlign = 'left';

        // Peak legend (blue)
        ctx.fillStyle = 'rgba(96, 165, 250, 0.8)';
        ctx.fillRect(10, 6, 12, 8);
        ctx.fillStyle = 'rgba(255, 255, 255, 0.7)';
        ctx.fillText('Peak', 26, 12);

        // RMS legend (orange)
        ctx.fillStyle = 'rgba(251, 146, 60, 0.9)';
        ctx.fillRect(60, 6, 12, 8);
        ctx.fillStyle = 'rgba(255, 255, 255, 0.7)';
        ctx.fillText('RMS', 76, 12);

        // Quality overlay legend (display mode only)
        if (mode === 'display') {
            const hasOverlays = (quality.contamination?.regions?.length > 0) || (quality.artifacts?.regions?.length > 0);
            if (hasOverlays) {
                ctx.textAlign = 'right';
                let legendY = 12;
                if (quality.contamination?.regions?.length > 0) {
                    ctx.fillStyle = 'rgba(251, 191, 36, 0.8)';
                    ctx.fillRect(width - 85, legendY - 8, 10, 10);
                    ctx.fillStyle = 'rgba(255, 255, 255, 0.7)';
                    ctx.fillText('Contamination', width - 5, legendY);
                    legendY += 14;
                }
                if (quality.artifacts?.regions?.length > 0) {
                    ctx.fillStyle = 'rgba(248, 113, 113, 0.8)';
                    ctx.fillRect(width - 85, legendY - 8, 10, 10);
                    ctx.fillStyle = 'rgba(255, 255, 255, 0.7)';
                    ctx.fillText('Artifacts', width - 5, legendY);
                }
            }
        }
    }

    function formatTime(seconds) {
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        const ms = Math.floor((seconds % 1) * 100);
        if (mins > 0) {
            return `${mins}:${secs.toString().padStart(2, '0')}.${ms.toString().padStart(2, '0')}`;
        }
        return `${secs}.${ms.toString().padStart(2, '0')}`;
    }

    function getSelection(state) {
        const { duration, selectionStart, selectionEnd } = state;
        return {
            startTime: selectionStart * duration,
            endTime: selectionEnd * duration,
            duration: (selectionEnd - selectionStart) * duration,
        };
    }

    function setSelection(state, startTime, endTime) {
        const { duration } = state;
        state.selectionStart = Math.max(0, Math.min(1, startTime / duration));
        state.selectionEnd = Math.max(0, Math.min(1, endTime / duration));

        // Ensure minimum selection
        const minSelection = MIN_SELECTION_SEC / duration;
        if (state.selectionEnd - state.selectionStart < minSelection) {
            state.selectionEnd = Math.min(1, state.selectionStart + minSelection);
        }

        draw(state);
        state.onChange(getSelection(state));
    }

    function setAudioElement(state, audioElement) {
        state.audioElement = audioElement;

        if (audioElement) {
            audioElement.addEventListener('timeupdate', () => {
                const { selectionStart, duration } = state;
                const selectionStartTime = selectionStart * duration;
                state.playbackPosition = audioElement.currentTime - selectionStartTime;
                draw(state);
            });

            audioElement.addEventListener('play', () => {
                state.isPlaying = true;
                draw(state);
            });

            audioElement.addEventListener('pause', () => {
                state.isPlaying = false;
                draw(state);
            });

            audioElement.addEventListener('ended', () => {
                state.isPlaying = false;
                state.playbackPosition = 0;
                draw(state);
            });
        }
    }

    function destroy(state) {
        const { canvas, resizeObserver, animationFrame } = state;

        if (resizeObserver) {
            resizeObserver.disconnect();
        }

        if (animationFrame) {
            cancelAnimationFrame(animationFrame);
        }

        // Remove event listeners
        canvas.onmousedown = null;
        canvas.onmousemove = null;
        canvas.onmouseup = null;
        canvas.onmouseleave = null;
        canvas.ontouchstart = null;
        canvas.ontouchmove = null;
        canvas.ontouchend = null;

        instances.delete(canvas);
    }

    // Public API
    return {
        init,
        getSelection: (canvas) => {
            const state = instances.get(canvas);
            return state ? getSelection(state) : null;
        },
        setSelection: (canvas, start, end) => {
            const state = instances.get(canvas);
            if (state) setSelection(state, start, end);
        },
        destroy: (canvas) => {
            const state = instances.get(canvas);
            if (state) destroy(state);
        },
    };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = WaveformTrimmer;
}
