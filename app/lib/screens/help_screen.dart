import 'package:flutter/material.dart';

import '../widgets.dart';

class HelpScreen extends StatelessWidget {
  const HelpScreen({super.key});

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Help')),
        body: SafeArea(
          child: ListView(padding: const EdgeInsets.all(16), children: const [
            SectionCard(
              title: 'How it works',
              icon: Icons.help_outline,
              child: Text(
                '1. Open New Meeting and enter your name, the Jitsi meeting link and the backend server URL.\n'
                '2. Tap Test to check the backend, then Start Meeting. A bot joins the Jitsi room and captures the audio.\n'
                '3. When the meeting ends (or you tap End meeting), the discussion is transcribed and the minutes are generated.\n'
                '4. Find every saved meeting under Past Meetings, where you can read and export the minutes.',
              ),
            ),
            SectionCard(
              title: 'Consent',
              icon: Icons.privacy_tip_outlined,
              child: Text('Only record meetings when all participants agree. Raw audio is deleted after processing; only the finished minutes and transcript are kept.'),
            ),
            SectionCard(
              title: 'If something goes wrong',
              icon: Icons.build_outlined,
              child: Text(
                '• Test fails: check the server URL, that the backend is running and that your phone can reach it.\n'
                '• Waiting for host approval: ask the host to admit the bot from the Jitsi lobby.\n'
                '• Meeting needs a password: the bot cannot join password-protected rooms.\n'
                '• No audio confirmed: the bot is in the room but nobody has spoken, or audio capture is unavailable.\n'
                '• Participant names missing: Jitsi did not expose them; speech is labelled generically.',
              ),
            ),
          ]),
        ),
      );
}
