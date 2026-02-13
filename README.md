# meetscribe

Pipeline d'analyse de meetings qui transforme des enregistrements vidéo (screen recordings) ou fichiers audio (mémos vocaux) en rapports structurés et détaillés.

## Fonctionnement

- Transcription audio via [Voxtral](https://mistral.ai/) (Mistral AI)
- Analyse visuelle des partages d'écran (pour les vidéos)
- Segmentation thématique et traitement parallèle par sous-agents
- Rapport final avec points clés, décisions, actions, screenshots et transcription corrigée

## Prérequis

- [Claude Code](https://docs.claude.com/en/api/agent-sdk) (CLI)
- [uv](https://docs.astral.sh/uv/)
- ffmpeg
- Clé API Mistral (`MISTRAL_API_KEY`)

## Usage

```bash
# Screen recording
meetscribe -i ~/Videos/reunion.mov --context "Johann est le prestataire, Jason le client"

# Mémo vocal
meetscribe -i ~/Downloads/memo.opus

# Avec dossier de sortie spécifique
meetscribe -i meeting.mp4 -o ~/docs/meetings
```

## Installation

```bash
git clone https://github.com/partageit/meetscribe.git
cd meetscribe
uv sync
cp .env.example .env  # Ajouter MISTRAL_API_KEY
```

Ajouter l'alias dans `~/.zshrc` :
```bash
alias meetscribe='uv run --project /path/to/meetscribe /path/to/meetscribe/main.py'
```
