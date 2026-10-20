import 'package:flutter/material.dart';

import '../services.dart';
import 'help_screen.dart';
import 'new_meeting_screen.dart';
import 'past_meetings_screen.dart';
import 'settings_screen.dart';

class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final auth = Services.of(context).auth;
    final user = auth.user;
    final name = (user?.displayName?.trim().isNotEmpty ?? false) ? user!.displayName! : (user?.email ?? 'there');

    Widget tile(IconData icon, String label, Widget Function() page) => Card(
          child: InkWell(
            borderRadius: BorderRadius.circular(12),
            onTap: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => page())),
            child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
              Icon(icon, size: 36, color: Theme.of(context).colorScheme.primary),
              const SizedBox(height: 8),
              Text(label, style: Theme.of(context).textTheme.titleMedium),
            ]),
          ),
        );

    return Scaffold(
      appBar: AppBar(
        title: const Text('Smart Meeting Minutes'),
        actions: [
          PopupMenuButton<String>(
            icon: const Icon(Icons.account_circle_outlined),
            tooltip: 'Account',
            onSelected: (v) async {
              if (v == 'logout') await auth.signOut();
            },
            itemBuilder: (_) => [
              PopupMenuItem<String>(enabled: false, child: Text(user?.email ?? 'Signed in')),
              const PopupMenuItem<String>(value: 'logout', child: Text('Log out')),
            ],
          ),
        ],
      ),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('Welcome, $name', style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 16),
            Expanded(
              child: GridView.count(
                crossAxisCount: 2,
                mainAxisSpacing: 12,
                crossAxisSpacing: 12,
                children: [
                  tile(Icons.video_call_outlined, 'New Meeting', () => const NewMeetingScreen()),
                  tile(Icons.history, 'Past Meetings', () => const PastMeetingsScreen()),
                  tile(Icons.settings_outlined, 'Settings', () => const SettingsScreen()),
                  tile(Icons.help_outline, 'Help', () => const HelpScreen()),
                ],
              ),
            ),
          ]),
        ),
      ),
    );
  }
}
