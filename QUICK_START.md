# 🚀 Quick Start — Get RAG Running in 5 Minutes

Follow these steps to run RAG locally and see it in action.

---

## Step 1: Start Ollama (if not already running)

Open a terminal and run:

```bash
ollama serve
```

You should see:
```
Listening on 127.0.0.1:11434
```

Leave this terminal open.

**First time?** Pull a model first:
```bash
ollama pull mistral-small3.1
```

---

## Step 2: Start RAG

Open a **new terminal** and navigate to the RAG folder:

```bash
cd "C:\Users\Landon\OneDrive\Documents\Ai Projects\RAG"
```

Then run:

**Windows:**
```bash
run.bat
```

**Linux/macOS:**
```bash
bash run.sh
```

You should see:
```
✅ Everything ready!

🌐 Starting Flask server on http://localhost:5000
   Press Ctrl+C to stop
```

---

## Step 3: Open in Browser

Go to: **http://localhost:5000**

You should see the RAG Assistant interface with 4 nav buttons:
- 💬 **Chat**
- 📚 **Library**
- ➕ **Add Sources**
- ⚙️ **Settings**

---

## Step 4: Add a Test Document

### Option A: Upload a PDF

1. Click **Add Sources**
2. Click **Upload File** tab
3. Select any PDF from your computer
4. Give it a title (e.g., "Test Document")
5. Click **Upload & Ingest**

Wait 10-30 seconds for embedding (progress shown in browser).

### Option B: Add a Web URL

1. Click **Add Sources**
2. Click **Add URL** tab
3. Paste a URL (e.g., `https://en.wikipedia.org/wiki/Artificial_intelligence`)
4. Give it a title
5. Click **Fetch & Ingest**

### Option C: Use a Sample Document

No files? Use this simple test:
1. Create a text file `test.txt`:
   ```
   RAG stands for Retrieval-Augmented Generation.
   It combines document search with language models.
   RAG improves accuracy by grounding answers in sources.
   ```
2. Upload it via **Add Sources** → **Upload File**

---

## Step 5: Ask a Question

1. Click **Chat**
2. Type a question about your document, e.g.:
   - "What is RAG?"
   - "Summarize the document"
   - "What does RAG stand for?"
3. Press Send or click the Send button
4. Watch it retrieve relevant chunks and generate an answer

You should see:
- The **answer** on the left
- **Sources** (with citations) on the right
- Each source shows doc type, title, and link

---

## Step 6: Manage Your Library

1. Click **Library**
2. You'll see all indexed documents with:
   - Document name
   - Type (pdf, txt, url)
   - Number of chunks indexed
   - Links to source

3. Click **Re-index All** to rebuild the vector index (takes longer for large docs)

---

## Step 7: Configure Settings (Optional)

1. Click **Settings**
2. **LLM Model** — select from models in your Ollama instance
3. **Library Location** — change where documents are stored (requires restart)

---

## 🎯 What You're Seeing

### How It Works

```
Your Question
    ↓
[Search in vector DB]
    ↓
[Rank results with keyword matching]
    ↓
[Pass top chunks + question to Ollama]
    ↓
[LLM generates answer with citations]
    ↓
You see answer + sources
```

### The Stack

- **Flask** — web server
- **ChromaDB** — vector database (stores document embeddings)
- **BAAI/bge embeddings** — converts text to vectors
- **Ollama + Mistral** — generates answers
- **BM25** — keyword ranking for precision

---

## 🔧 Troubleshooting

### "Cannot connect to Ollama"
- Make sure `ollama serve` is running in another terminal
- Check http://localhost:11434 is accessible

### "No sources found when searching"
- Upload a document first via **Add Sources**
- Wait for embedding to complete (10-30s depending on file size)
- Refresh the page

### "Model not found"
- In Settings, check which model is selected
- Make sure it's pulled: `ollama pull mistral-small3.1`

### App is slow
- Large PDFs take time to embed
- First query is slower (initializes embedder)
- Subsequent queries are faster

---

## 📚 Next Steps

### Explore Features

1. **Upload multiple documents** — try PDFs, TXT, and URLs
2. **Multi-turn chat** — ask follow-up questions (history preserved)
3. **Delete documents** — go to Add Sources → Manage tab
4. **Change models** — try different Ollama models in Settings

### Read More

- **Full guide:** See `README.md`
- **Setup details:** See `DEPLOYMENT.md`
- **Next version:** See `PLAN.md` (FastAPI + Docker)

### Stop the App

In the Flask terminal, press **Ctrl+C**

---

## 🚀 Ready to Go Production?

Once you've tested locally and want to deploy with:
- ✅ Multiple users
- ✅ Docker containers
- ✅ PostgreSQL auth
- ✅ HTTPS
- ✅ Job queue for ingestion

See `PLAN.md` for the v2.0 FastAPI + Docker roadmap.

---

**Questions?** Check `README.md` or `DEPLOYMENT.md` for detailed guides.
