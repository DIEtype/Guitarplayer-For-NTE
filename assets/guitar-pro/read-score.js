// Minimal host for alphaTab's file reader. No audio, browser or network APIs.
var exports = {};
var module = { exports };
var process = { [Symbol.toStringTag]: 'process' };

class TextDecoder {
    constructor(encoding = 'utf-8') {
        this.encoding = encoding.toLowerCase();
    }

    decode(buffer) {
        const bytes = new Uint8Array(buffer);
        const characters = [];
        const windows1252 = '€\u0081‚ƒ„…†‡ˆ‰Š‹Œ\u008dŽ\u008f\u0090‘’“”•–—˜™š›œ\u009džŸ';
        for (let i = 0; i < bytes.length;) {
            const first = bytes[i++];
            let code = first;
            if (this.encoding === 'utf-16le' || this.encoding === 'utf-16be') {
                if (i === bytes.length) { characters.push('\uFFFD'); break; }
                const second = bytes[i++];
                code = this.encoding === 'utf-16le' ? first + 256 * second : 256 * first + second;
            } else if (this.encoding === 'windows-1252') {
                if (first >= 128 && first < 160) code = windows1252.charCodeAt(first - 128);
            } else if (first >= 128) {
                const count = first >= 194 && first <= 223 ? 1 : first >= 224 && first <= 239 ? 2 : first >= 240 && first <= 244 ? 3 : 0;
                if (!count) { characters.push('\uFFFD'); continue; }
                code = first & (127 >> (count + 1));
                let valid = true;
                for (let j = 0; j < count; j++) {
                    const next = bytes[i];
                    if ((next & 192) !== 128 || (j === 0 && (
                        (first === 224 && next < 160) || (first === 237 && next > 159) ||
                        (first === 240 && next < 144) || (first === 244 && next > 143)))) {
                        valid = false;
                        break;
                    }
                    i++;
                    code = (code << 6) | (next & 63);
                }
                if (!valid) code = 65533;
            }
            if (code !== 65279 || characters.length) characters.push(String.fromCodePoint(code));
        }
        return characters.join('');
    }
}

function readGuitarPro(encoded, encoding) {
    const alphaTab = module.exports;
    const settings = new alphaTab.Settings();
    settings.importer.encoding = encoding;
    const score = alphaTab.importer.ScoreLoader.loadScoreFromBytes(Uint8Array.fromBase64(encoded), settings);
    const masterBars = score.masterBars;
    if (!masterBars.length || masterBars.length > 25000) throw new Error('小节数超出可导入范围');
    const lengths = masterBars.map(bar => bar.calculateDuration());
    let order = masterBars.map(bar => bar.index);
    if (masterBars.some(bar => bar.repeatCount > 1 || bar.alternateEndings || bar.directions?.size)) {
        const handler = new Proxy({}, { get: () => () => {} });
        const generator = new alphaTab.midi.MidiFileGenerator(score, settings, handler);
        generator.generate();
        order = generator.tickLookup.masterBars.map(entry => entry.masterBar.index);
    }
    const tracks = [];
    let totalNotes = 0;
    for (const track of score.tracks) {
        for (const [staffIndex, staff] of track.staves.entries()) {
            const name = track.name || `轨道 ${track.index + 1}`;
            const label = track.staves.length > 1 ? `${name} · 谱表 ${staffIndex + 1}` : name;
            const supported = !staff.isPercussion && staff.tuning.length === 6;
            let noteCount = 0;
            const bars = supported ? staff.bars.map(bar => {
                const beats = [];
                for (const voice of bar.voices) {
                    for (const beat of voice.beats) {
                        if (!beat.notes.length && !beat.chord) continue;
                        noteCount += beat.notes.length;
                        totalNotes += beat.notes.length;
                        if (totalNotes > 150000) throw new Error('文件音符过多，请在 Guitar Pro 中分段');
                        const notes = beat.notes.map(note => [
                            7 - note.string, note.fret, note.id, note.tieOrigin?.id ?? null,
                            note.isDead, note.hasBend || note.isHarmonic || note.slideOutType ||
                            note.slideInType || note.vibrato || note.isLetRing || note.isPalmMute || note.isTrill ||
                            note.isHammerPullOrigin,
                            note.hasBend ? note.bendPoints.map(point => [point.offset, point.value]) : null,
                            Boolean(note.slideOutType || note.slideInType)
                        ]);
                        const brush = beat.brushType === alphaTab.model.BrushType.BrushUp ? 'up' :
                            beat.brushType === alphaTab.model.BrushType.BrushDown ? 'down' : '';
                        beats.push([beat.playbackStart, beat.playbackDuration, beat.chord?.name || '', notes,
                            Boolean((beat.brushType && !brush) || beat.vibrato || beat.fermata || beat.whammyBarPoints?.length), brush]);
                    }
                }
                return beats.sort((a, b) => a[0] - b[0]);
            }) : [];
            tracks.push({label, supported, noteCount, bars, tuning: staff.tuning, capo: staff.capo,
                transposition: staff.transpositionPitch, program: track.playbackInfo.program});
        }
    }
    return JSON.stringify({title: score.title, tempo: score.tempo, tracks, order,
        bars: masterBars.map((bar, index) => [bar.timeSignatureNumerator, bar.timeSignatureDenominator,
            lengths[index], bar.tempoAutomations.map(a => [a.ratioPosition, a.value]), Boolean(bar.tripletFeel)])});
}
