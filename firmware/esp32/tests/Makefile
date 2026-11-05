# Host-side firmware tests: make -C firmware/esp32/tests
# Builds and runs the suite twice, once per framing.
CXX ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Werror
SRC = test_protocol.cpp ../lora_protocol.cpp ../poll_receiver.cpp ../ack_receiver.cpp
HDR = ../lora_protocol.h ../mac.h ../calibration.h ../sensor_drivers.h

test_protocol_15: $(SRC) $(HDR)
	$(CXX) $(CXXFLAGS) -DFRAME_MODE=15 -I.. $(SRC) -o $@

test_protocol_3: $(SRC) $(HDR)
	$(CXX) $(CXXFLAGS) -DFRAME_MODE=3 -I.. $(SRC) -o $@

test: test_protocol_15 test_protocol_3
	@printf 'FRAME_MODE 15: ' && ./test_protocol_15
	@printf 'FRAME_MODE 3:  ' && ./test_protocol_3

clean:
	rm -f test_protocol test_protocol_15 test_protocol_3

.PHONY: test clean
.DEFAULT_GOAL := test
