# Example session

Below is an annotated transcript of a typical agent run inside the Telegram chat. It shows
how the classifier, sandbox, and audit log work together in practice. Each line is logged
with its file path, action, and result, then rendered to the user in plain language.

```
User:   List files in the sandbox
Agent:  [AUTO] list_files -> sandbox/demo/ (3 items)
        audit: action=list_files path=sandbox/demo result=ok

User:   Create a file C:\private-ai\sandbox\notes.txt with text "hello"
Agent:  [CONFIRM] create_file -> sandbox\notes.txt, content="hello"
        Please approve this write in Telegram to proceed.
[ user approves ]
Agent:  [CONFIRM OK] wrote 1 file (sandbox\notes.txt)
        audit: action=create_file path=sandbox\notes.txt result=ok

User:   Read C:\Users\haykm\notes.txt
Agent:  [FORBIDDEN] blocked — outside sandbox contour (user-home path).
        audit: action=read path=C:\Users\haykm\notes.txt result=blocked

User:   rm -rf C:\private-ai\sandbox
Agent:  [FORBIDDEN] blocked — destructive, system-level prohibition.
        audit: action=rm result=blocked
```

### What this demonstrates

- **Read-only by default**: listing and reading run without asking (AUTO).
- **Approval before writes**: creating files inside the sandbox asks for inline approval (CONFIRM).
- **Hard boundaries**: user-home paths and recursive deletes are refused even if the model tries (FORBIDDEN).
- **Plain-language audit**: every step is recorded with path + result and shown in chat.
