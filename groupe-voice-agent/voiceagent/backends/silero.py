from voiceagent.domain import Samples


class Silero:
    def __init__(self):
        import torch
        from silero_vad import load_silero_vad

        self._torch = torch
        torch.set_num_threads(1)
        self._model = load_silero_vad(onnx=True)

    def probability(self, samples: Samples) -> float:
        tensor = self._torch.from_numpy(samples)
        return float(self._model(tensor, 16000).item())

    def reset(self) -> None:
        self._model.reset_states()
