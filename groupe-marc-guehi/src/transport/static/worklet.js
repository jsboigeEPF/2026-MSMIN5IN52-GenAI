// AudioWorklet : micro (fréquence de la carte son) -> PCM16 mono 16 kHz, trames de 512 échantillons (32 ms).
class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.pos = 0;
    this.acc = 0;
    this.cnt = 0;
    this.out = new Int16Array(512);
    this.n = 0;
    this.sumSq = 0;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.acc += ch[i];
      this.cnt++;
      this.pos += 1;
      if (this.pos >= this.ratio) {  // moyenne sur la fenêtre = filtre passe-bas simple avant décimation
        this.pos -= this.ratio;
        const v = Math.max(-1, Math.min(1, this.acc / this.cnt));
        this.acc = 0;
        this.cnt = 0;
        this.out[this.n++] = Math.round(v * 32767);
        this.sumSq += v * v;
        if (this.n === 512) {
          this.port.postMessage({ pcm: this.out.buffer.slice(0), rms: Math.sqrt(this.sumSq / 512) });
          this.n = 0;
          this.sumSq = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("pcm-processor", PCMProcessor);
