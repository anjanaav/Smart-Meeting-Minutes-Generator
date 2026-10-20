
import 'package:flutter_test/flutter_test.dart';
import 'package:smart_meeting_minutes/main.dart';

void main() {
  testWidgets('SMMG app displays Firebase configuration error when Firebase is unavailable',
      (WidgetTester tester) async {
    await tester.pumpWidget(
      const SmmgApp(
        firebaseError: 'Firebase is not configured for this build.',
      ),
    );

    expect(
      find.text('Firebase is not configured for this build.'),
      findsOneWidget,
    );

    expect(tester.takeException(), isNull);
  });
}
