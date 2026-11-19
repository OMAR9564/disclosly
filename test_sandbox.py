from agent.calculations import CalculationSandbox

sandbox = CalculationSandbox()
res = sandbox.run_code('result = inputs["revenue"] * inputs["margin"]', {"revenue": 1000000, "margin": 0.25})
print("Sandbox Result:", res)