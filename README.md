# bazaar-contrib-server-conversation

The conversations of a Bazaar Compute server: every agent's chats on the computers it is connected to, side by side, with their messages and the pictures in them, the first contacts waiting to be let in, what an agent says to them meanwhile, and a search through all of them. Everything stays on the computers and is read there; a computer that cannot answer yet has bazaar-contrib-starlink-conversation installed the first time it is asked.

```toml
[[contribs]]
package = "bazaar-contrib-server-conversation~=0.3.0rc1"
[[contribs.enabled]]
entry = "server-conversation"
```

A contrib reads how many conversations wait for a decision by accepting `CONVERSATIONS`.
